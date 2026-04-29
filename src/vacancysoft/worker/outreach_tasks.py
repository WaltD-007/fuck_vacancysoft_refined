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

from sqlalchemy import func, or_, select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import ReceivedReply, SentMessage, User
from vacancysoft.outreach import GraphClient, GraphError
from vacancysoft.outreach.tracking import (
    inject_pixel,
    is_tracking_enabled,
    rewrite_links,
)

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


def _resolve_sender_email(session, sender_user_id: str) -> str:
    """Map a ``sender_user_id`` to the operator's mailbox email.

    ``sender_user_id`` in :class:`SentMessage` is whatever the launch
    endpoint stamped — Entra object-id when SSO has populated it, else
    UPN/email. Both forms are accepted on the launch path.

    The reply poller needs the actual mailbox **email** to filter out
    self-replies (Graph's ``conversationId`` returns both sides of the
    thread, so the operator's own outbound copy can show up in the
    poll). Comparing the raw ``sender_user_id`` (likely an OID) to a
    ``from_email`` (always an email) never matches, which is the bug
    this helper fixes.

    Resolution order:
      1. ``users.entra_object_id`` exact match → return ``users.email``
      2. ``users.email`` exact match (lowercased) → return that email
      3. No match → return the raw ``sender_user_id`` lowercased
         (best-effort fallback so a missing user row degrades to the
         pre-fix behaviour rather than crashing)

    Always returns a lowercase string, ready to compare against
    ``from_email.lower()``.
    """
    needle = (sender_user_id or "").strip()
    if not needle:
        return ""
    user = session.execute(
        select(User).where(
            or_(
                User.entra_object_id == needle,
                func.lower(User.email) == needle.lower(),
            )
        )
    ).scalar_one_or_none()
    if user is not None and user.email:
        return user.email.strip().lower()
    return needle.lower()


# ── Helper: schedule a 5-email sequence ───────────────────────────────


async def schedule_outreach_sequence(
    *,
    redis,
    session,
    campaign_output_id: str,
    sender_user_id: str,
    recipient_email: str,
    tones: list[str],
    emails: list[dict[str, str]],
    cadence_days: list[int] | None = None,
    recipient_name: str | None = None,
) -> list[str]:
    """Create :class:`SentMessage` rows + deferred ARQ jobs for one arc.

    ``emails`` must be a list of 5 dicts each with ``subject`` + ``body``
    keys, ordered step 1..5. Caller is responsible for picking the
    right variant per step from
    ``campaign_output.outreach_emails[i].variants[tones[i]]``.

    ``tones`` must be a length-5 list of tone identifiers (one per step,
    matching ``emails`` index-for-index). The Builder lets each step
    pick its own tone, so each SentMessage row stamps its own value.
    Pre-2026-04-29 callers passing a single ``tone`` string and
    expecting it to broadcast across all 5 steps must update — the
    launch endpoint does the broadcast for legacy clients but worker-
    side helpers no longer accept that shape.

    ``cadence_days`` defaults to ``[0, 7, 14, 21, 28]`` (from
    configs/app.toml). First value must be 0; values after that are
    day-offsets from the first send.

    ``recipient_name`` is the operator-verified hiring-manager name.
    Stored alongside ``recipient_email`` on every SentMessage row so
    the Campaigns tracker can display the verified value rather than
    re-deriving from the dossier on every render. ``None`` is fine —
    the tracker falls back to the dossier-derived name when this is
    NULL.

    Launch-grace period: ``[outreach] launch_grace_minutes`` (default
    10) is applied as a base offset before the cadence day-offsets,
    so step 1 fires at +grace_min from now rather than instantly. This
    gives the operator a window to spot an obvious mistake (wrong
    tone, wrong recipient) and hit Stop before any mail leaves.

    Returns the list of sent_message_ids in sequence order.

    Safe to call in dry-run — rows are created and ARQ jobs are
    scheduled as normal; the worker will use the canned Graph path
    on each fire.
    """
    if len(emails) != 5:
        raise ValueError(f"expected 5 emails in sequence, got {len(emails)}")
    if len(tones) != 5:
        raise ValueError(f"expected 5 tones (one per step), got {len(tones)}")

    cfg = _load_outreach_config()
    cadence = cadence_days or cfg.get("default_cadence_days", [0, 7, 14, 21, 28])
    if len(cadence) != 5 or cadence[0] != 0:
        raise ValueError(
            f"cadence_days must be length-5 starting with 0, got {cadence!r}"
        )

    grace_minutes = int(cfg.get("launch_grace_minutes", 10))
    base_time = datetime.utcnow() + timedelta(minutes=grace_minutes)
    sent_message_ids: list[str] = []

    name_clean = (recipient_name or "").strip() or None

    for i, email in enumerate(emails, start=1):
        scheduled_for = base_time + timedelta(days=cadence[i - 1])
        sent_message_id = str(uuid4())
        row = SentMessage(
            id=sent_message_id,
            campaign_output_id=campaign_output_id,
            sender_user_id=sender_user_id,
            recipient_email=recipient_email,
            recipient_name=name_clean,
            sequence_index=i,
            tone=tones[i - 1],
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
        "outreach.schedule_outreach_sequence campaign=%s tones=%s scheduled %d rows grace_min=%d",
        campaign_output_id, ",".join(tones), len(sent_message_ids), grace_minutes,
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

        # Open + click tracking. Inject the 1×1 pixel and rewrite any
        # http(s) links to point at /t/c/<token> before handing the body
        # to Graph. Both calls are no-ops when OUTREACH_TRACKING_ENABLED
        # is set to a falsy value — kill switch for "is the issue
        # tracking?" debugging without needing a code change.
        #
        # We mutate row.body so the as-sent body is what's stored on
        # the SentMessage row. Useful for "why does this email show 5
        # opens?" debugging — the stored body matches what arrived in
        # the recipient's inbox.
        if is_tracking_enabled():
            base_url = os.environ.get("TRACKING_DOMAIN", "http://localhost:8000").rstrip("/")
            row.body = inject_pixel(row.body, row.id, base_url)
            row.body = rewrite_links(row.body, row.id, base_url)
            s.flush()

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

        # Resolve sender_user_id (likely an Entra OID) → operator email
        # so the self-reply filter below has something it can actually
        # compare against from_email. Done inside the existing session
        # so we don't open another DB connection.
        sender_email = _resolve_sender_email(s, sender_user_id)

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

    # Filter out any replies that came from our own operator (Graph's
    # conversationId spans both sides of the thread, so the operator's
    # own outbound copy can show up in the poll).
    #
    # Compare against ``sender_email`` (resolved from the users table
    # above). The previous version compared against ``sender_user_id``
    # directly, which is broken once SSO populates that field with an
    # Entra object-id — OID would never equal an email and self-replies
    # would always trigger sequence cancellation.
    non_self_replies = [
        r for r in replies
        if (r.get("from_email") or "").strip().lower() != sender_email
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
