"""ARQ tasks for the Microsoft Graph outreach email stack.

Four public functions — three are registered with ARQ directly, the
fourth is a pure helper used by the API layer to schedule a sequence.

Registered ARQ tasks:

- :func:`send_outreach_email` — fires at each scheduled send time.
  Reads the :class:`SentMessage` row, calls
  :meth:`GraphClient.send_mail`, updates the row with returned ids.
  On success, enqueues the first reply-poll at +10 minutes.

- :func:`poll_replies_for_conversation` — runs every
  ``[outreach] poll_interval_minutes`` per conversation. Calls
  :meth:`GraphClient.list_replies`; if any reply lands, inserts
  :class:`ReceivedReply` rows and cancels the remaining pending
  sequence. Re-enqueues itself at +interval minutes until either
  a reply lands or we hit ``poll_max_days``.

Helper (called from the API, not registered with ARQ):

- :func:`schedule_outreach_sequence` — creates the 5 :class:`SentMessage`
  rows for a campaign + tone + cadence, registers a deferred ARQ job
  per row, returns the list of sent_message_ids.

All four functions are safe to call in dry-run mode (the default) —
``GraphClient`` short-circuits its I/O before any network call, and
DB writes still happen so the lifecycle can be exercised end-to-end.

Design notes that matter for the operator:

1. **Deferred jobs use ARQ's native scheduling.** ``enqueue_job(...,
   _defer_until=<dt>)`` stashes the job in a Redis sorted-set keyed by
   fire-time. No extra cron infrastructure.

2. **Cancellation is ARQ-native.** When a reply lands we call
   ``redis.abort_job(job_id)`` on each pending sequence item. That
   atomically removes the deferred job so it never fires. The DB row
   is then flipped to ``cancelled_replied``.

3. **Polling is self-re-enqueuing, bounded.** Each poll either finds
   a reply (stops polling by not re-enqueuing) or re-enqueues itself
   at +interval. A hard ceiling at ``poll_max_days`` since the first
   send prevents runaway polling on stale conversations.

4. **Idempotency.** If the worker restarts mid-send, ARQ re-fires
   the job with the same job_id. ``send_outreach_email`` checks
   ``sent_messages.status`` at the top and exits early if it's
   already ``'sent'`` — no duplicate-send on restart.
"""

from __future__ import annotations

import logging
import os
import tomllib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import ReceivedReply, SentMessage
from vacancysoft.outreach import GraphClient, GraphError

logger = logging.getLogger(__name__)


DEFAULT_POLL_INTERVAL_MINUTES = 10
DEFAULT_POLL_MAX_DAYS = 90


def _load_outreach_config() -> dict[str, Any]:
    """Read ``[outreach]`` from configs/app.toml. Missing section is OK
    — we fall back to the defaults above."""
    try:
        with open("configs/app.toml", "rb") as f:
            return tomllib.load(f).get("outreach", {})
    except Exception:
        return {}


# ── Helper: schedule a 5-email sequence ───────────────────────────────


async def schedule_outreach_sequence(
    *,
    redis,
    session,
    campaign_output_id: str,
    sender_user_id: str,
    recipient_email: str,
    tone: str,
    emails: list[dict[str, str]],
    cadence_days: list[int] | None = None,
) -> list[str]:
    """Create :class:`SentMessage` rows + deferred ARQ jobs for one arc.

    ``emails`` must be a list of 5 dicts each with ``subject`` + ``body``
    keys (e.g. ``campaign_output.outreach_emails[tone][i]`` for i in
    0..4). This function does NOT generate content — that's already
    done at campaign-creation time.

    ``cadence_days`` defaults to ``[0, 7, 14, 21, 28]`` (from
    configs/app.toml). First value must be 0; values after that are
    day-offsets from the first send.

    Returns the list of sent_message_ids in sequence order.

    Safe to call in dry-run — rows are created and ARQ jobs are
    scheduled as normal; the worker will use the canned Graph path
    on each fire.
    """
    if len(emails) != 5:
        raise ValueError(f"expected 5 emails in sequence, got {len(emails)}")

    cfg = _load_outreach_config()
    cadence = cadence_days or cfg.get("default_cadence_days", [0, 7, 14, 21, 28])
    if len(cadence) != 5 or cadence[0] != 0:
        raise ValueError(
            f"cadence_days must be length-5 starting with 0, got {cadence!r}"
        )

    now = datetime.utcnow()
    sent_message_ids: list[str] = []

    for i, email in enumerate(emails, start=1):
        scheduled_for = now + timedelta(days=cadence[i - 1])
        sent_message_id = str(uuid4())
        row = SentMessage(
            id=sent_message_id,
            campaign_output_id=campaign_output_id,
            sender_user_id=sender_user_id,
            recipient_email=recipient_email,
            sequence_index=i,
            tone=tone,
            scheduled_for=scheduled_for,
            status="pending",
            subject=email.get("subject", ""),
            body=email.get("body", ""),
        )
        session.add(row)
        session.flush()

        # ARQ deferred job. Job id includes the sent_message_id so
        # idempotent retries don't double-fire.
        job = await redis.enqueue_job(
            "send_outreach_email",
            sent_message_id,
            _defer_until=scheduled_for,
            _job_id=f"send-{sent_message_id}",
        )
        if job is not None:
            row.arq_job_id = job.job_id
        sent_message_ids.append(sent_message_id)

    session.commit()
    logger.info(
        "outreach.schedule_outreach_sequence campaign=%s tone=%s scheduled %d rows",
        campaign_output_id, tone, len(sent_message_ids),
    )
    return sent_message_ids


# ── ARQ task: send one email ──────────────────────────────────────────


async def send_outreach_email(ctx: dict[str, Any], sent_message_id: str) -> None:
    """Worker-side handler for one scheduled send.

    Fires at ``scheduled_for``. Reads the row, short-circuits if already
    sent or cancelled, calls Graph, records outcome, enqueues the
    first reply-poll.

    Idempotent via DB status check: if the row is already ``'sent'``
    (because this job was retried by ARQ after a worker crash mid-send),
    we exit without calling Graph again.
    """
    with SessionLocal() as s:
        row = s.execute(
            select(SentMessage).where(SentMessage.id == sent_message_id)
        ).scalar_one_or_none()

        if row is None:
            logger.warning(
                "outreach.send_outreach_email row not found: %s", sent_message_id
            )
            return

        if row.status != "pending":
            logger.info(
                "outreach.send_outreach_email status=%s (not pending); "
                "exiting without resend", row.status,
            )
            return

        graph = GraphClient()
        try:
            result = await graph.send_mail(
                sender_user_id=row.sender_user_id,
                to_address=row.recipient_email,
                subject=row.subject,
                html_body=row.body,
            )
        except GraphError as exc:
            logger.warning(
                "outreach.send_outreach_email graph error id=%s status=%s code=%s",
                sent_message_id, exc.status_code, exc.graph_error_code,
            )
            row.status = "failed"
            row.error_message = str(exc)[:1000]
            s.commit()
            return

        row.status = "sent"
        row.sent_at = datetime.utcnow()
        row.graph_message_id = result.get("graph_message_id") or None
        row.conversation_id = result.get("conversation_id") or None
        s.commit()

    # Enqueue the first reply-poll +10 minutes out (outside the DB
    # session so we aren't holding a connection during Redis I/O).
    if row.conversation_id:
        cfg = _load_outreach_config()
        interval = int(cfg.get("poll_interval_minutes", DEFAULT_POLL_INTERVAL_MINUTES))
        poll_at = datetime.utcnow() + timedelta(minutes=interval)
        redis = ctx.get("redis")
        if redis is not None:
            await redis.enqueue_job(
                "poll_replies_for_conversation",
                row.conversation_id,
                row.sender_user_id,
                _defer_until=poll_at,
                _job_id=f"poll-{row.conversation_id}-{int(poll_at.timestamp())}",
            )

    logger.info(
        "outreach.send_outreach_email sent id=%s conv=%s",
        sent_message_id, row.conversation_id,
    )


# ── ARQ task: poll a conversation for replies ─────────────────────────


async def poll_replies_for_conversation(
    ctx: dict[str, Any],
    conversation_id: str,
    sender_user_id: str,
) -> None:
    """Check a conversation for new replies; cancel remaining sequence
    and stop polling if found; else re-enqueue self.

    Stops polling when either:
      (a) a reply lands (no re-enqueue), or
      (b) ``poll_max_days`` since the earliest send in this conversation.
    """
    cfg = _load_outreach_config()
    interval = int(cfg.get("poll_interval_minutes", DEFAULT_POLL_INTERVAL_MINUTES))
    max_days = int(cfg.get("poll_max_days", DEFAULT_POLL_MAX_DAYS))

    with SessionLocal() as s:
        # Earliest send in this conversation — drives both the "since"
        # filter for Graph and the poll-ceiling check.
        first_send = s.execute(
            select(SentMessage)
            .where(SentMessage.conversation_id == conversation_id)
            .where(SentMessage.sent_at.is_not(None))
            .order_by(SentMessage.sent_at)
            .limit(1)
        ).scalar_one_or_none()

        if first_send is None:
            # No sent messages in this conv — can't meaningfully poll.
            logger.warning(
                "outreach.poll_replies_for_conversation no sent rows for conv=%s",
                conversation_id,
            )
            return

        age = datetime.utcnow() - first_send.sent_at
        if age > timedelta(days=max_days):
            logger.info(
                "outreach.poll_replies_for_conversation conv=%s older than %dd; "
                "stopping polling", conversation_id, max_days,
            )
            return

        # Graph needs a tz-aware 'since' — use the earliest send time
        # minus a small buffer so we don't miss a reply that lands at
        # the exact send moment.
        since_dt = first_send.sent_at.replace(tzinfo=timezone.utc) - timedelta(minutes=1)

    graph = GraphClient()
    try:
        replies = await graph.list_replies(
            user_id=sender_user_id,
            conversation_id=conversation_id,
            since=since_dt,
        )
    except GraphError as exc:
        logger.warning(
            "outreach.poll_replies_for_conversation graph error conv=%s status=%s: %s",
            conversation_id, exc.status_code, exc,
        )
        # Re-enqueue — transient failures shouldn't kill polling.
        await _reschedule_poll(ctx, conversation_id, sender_user_id, interval)
        return

    # Filter out any replies that came from our own operator (auto-
    # replies like the sent-items echo). Graph's conversationId spans
    # both sides, so this is a safety filter.
    non_self_replies = [
        r for r in replies
        if (r.get("from_email") or "").lower() != sender_user_id.lower()
    ]

    if not non_self_replies:
        await _reschedule_poll(ctx, conversation_id, sender_user_id, interval)
        return

    # ── Reply observed — record + cancel remaining sequence ──
    with SessionLocal() as s:
        # Find the earliest pending sent-message in this conv (for
        # best-match FK).
        pending = s.execute(
            select(SentMessage)
            .where(SentMessage.conversation_id == conversation_id)
            .where(SentMessage.status == "pending")
            .order_by(SentMessage.sequence_index)
        ).scalars().all()

        matched_id = pending[0].id if pending else None

        for r in non_self_replies:
            # Dedupe by unique graph_message_id
            existing = s.execute(
                select(ReceivedReply).where(
                    ReceivedReply.graph_message_id == r.get("graph_message_id", "")
                )
            ).scalar_one_or_none()
            if existing:
                continue

            try:
                received_at_dt = datetime.fromisoformat(
                    (r.get("received_at") or "").replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                received_at_dt = datetime.utcnow()

            s.add(ReceivedReply(
                conversation_id=conversation_id,
                sender_user_id=sender_user_id,
                graph_message_id=r.get("graph_message_id", ""),
                from_email=r.get("from_email", ""),
                received_at=received_at_dt,
                subject=r.get("subject"),
                matched_sent_message_id=matched_id,
            ))

        # Cancel remaining pending sequence items
        redis = ctx.get("redis")
        for row in pending:
            if redis is not None and row.arq_job_id:
                try:
                    await redis.abort_job(row.arq_job_id)
                except Exception as exc:  # pragma: no cover — best-effort
                    logger.warning(
                        "outreach: failed to abort ARQ job %s: %s",
                        row.arq_job_id, exc,
                    )
            row.status = "cancelled_replied"

        s.commit()

    logger.info(
        "outreach.poll_replies_for_conversation conv=%s reply received; "
        "cancelled %d pending sends",
        conversation_id, len(pending),
    )


async def _reschedule_poll(
    ctx: dict[str, Any],
    conversation_id: str,
    sender_user_id: str,
    interval_minutes: int,
) -> None:
    """Re-enqueue self for the next polling tick."""
    poll_at = datetime.utcnow() + timedelta(minutes=interval_minutes)
    redis = ctx.get("redis")
    if redis is None:
        return
    await redis.enqueue_job(
        "poll_replies_for_conversation",
        conversation_id,
        sender_user_id,
        _defer_until=poll_at,
        _job_id=f"poll-{conversation_id}-{int(poll_at.timestamp())}",
    )


# ── Manual cancellation (called from the API when operator clicks ──
# "Cancel Sequence" in PR D) ──────────────────────────────────────


async def cancel_pending_sequence_manual(
    *,
    session,
    redis,
    campaign_output_id: str,
) -> int:
    """Cancel every ``pending`` SentMessage in a campaign.

    Called (awaited) from the API endpoint in PR D. Returns the
    number of rows cancelled. Does NOT record a ReceivedReply — this
    is an operator-initiated stop, not a reply-triggered one.
    """
    pending = session.execute(
        select(SentMessage)
        .where(SentMessage.campaign_output_id == campaign_output_id)
        .where(SentMessage.status == "pending")
    ).scalars().all()

    for row in pending:
        if redis is not None and row.arq_job_id:
            try:
                await redis.abort_job(row.arq_job_id)
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "outreach: failed to abort ARQ job %s: %s",
                    row.arq_job_id, exc,
                )
        row.status = "cancelled_manual"

    session.commit()
    return len(pending)
