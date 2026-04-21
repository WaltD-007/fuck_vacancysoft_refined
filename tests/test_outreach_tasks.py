"""Tests for the outreach ARQ tasks.

All tests run in dry-run mode (the default) — no real Graph calls, no
real Redis. We stub three things:

1. ``SessionLocal`` (in outreach_tasks) with a per-test in-memory SQLite
2. ``GraphClient`` (via monkeypatching the module-level reference) so
   we can control the send/list_replies responses directly
3. ``ctx['redis']`` with a :class:`_FakeRedis` that records
   enqueue_job + abort_job calls

No ARQ process is spawned — we call the task functions directly as
``await send_outreach_email(ctx, sent_message_id)``. This is the
exact signature ARQ uses, so we're exercising the same code path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    ReceivedReply,
    SentMessage,
)
from vacancysoft.worker import outreach_tasks


# ── Fakes ───────────────────────────────────────────────────────────


class _FakeRedisJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _FakeRedis:
    """Records enqueue/abort calls so tests can assert against them."""

    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self.aborted: list[str] = []

    async def enqueue_job(self, fn_name: str, *args: Any, **kwargs: Any):
        self.enqueued.append({"fn": fn_name, "args": args, "kwargs": kwargs})
        return _FakeRedisJob(job_id=kwargs.get("_job_id", f"arq-{len(self.enqueued)}"))

    async def abort_job(self, job_id: str) -> None:
        self.aborted.append(job_id)


class _FakeGraphClient:
    """Used in tests that need non-dry-run behaviour without real Graph."""

    def __init__(
        self,
        *,
        send_result: dict[str, Any] | None = None,
        list_result: list[dict[str, Any]] | None = None,
    ) -> None:
        self._send_result = send_result
        self._list_result = list_result or []
        self.send_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    async def send_mail(self, **kwargs: Any) -> dict[str, Any]:
        self.send_calls.append(kwargs)
        if self._send_result is not None:
            return self._send_result
        return {
            "graph_message_id": f"fake-msg-{len(self.send_calls)}",
            "conversation_id": f"fake-conv-{len(self.send_calls)}",
            "user_id": kwargs["sender_user_id"],
            "to_address": kwargs["to_address"],
            "subject": kwargs["subject"],
            "sent_at": datetime.utcnow().isoformat(),
            "dry_run": False,
        }

    async def list_replies(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append(kwargs)
        return self._list_result


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine, monkeypatch: pytest.MonkeyPatch):
    """Patch SessionLocal inside outreach_tasks so the task functions
    use our test DB. Each ``with SessionLocal() as s:`` call gets a
    fresh Session that shares the same engine."""
    Factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(outreach_tasks, "SessionLocal", Factory)
    return Factory


@pytest.fixture
def campaign_output(session_factory):
    """One CampaignOutput row to hang SentMessages off."""
    s = session_factory()
    co = CampaignOutput(
        dossier_id=f"stub-{uuid4().hex[:8]}",
        model_used="gpt-5.4",
    )
    s.add(co)
    s.commit()
    yield co
    s.close()


# ── schedule_outreach_sequence ──────────────────────────────────────


class TestScheduleSequence:

    @pytest.mark.asyncio
    async def test_creates_5_rows_with_correct_cadence(
        self, session_factory, campaign_output
    ) -> None:
        redis = _FakeRedis()
        s = session_factory()
        emails = [
            {"subject": f"Seq {i}", "body": f"<p>body {i}</p>"} for i in range(1, 6)
        ]

        ids = await outreach_tasks.schedule_outreach_sequence(
            redis=redis,
            session=s,
            campaign_output_id=campaign_output.id,
            sender_user_id="op-1",
            recipient_email="hm@corp.com",
            tone="formal",
            emails=emails,
            cadence_days=[0, 7, 14, 21, 28],
        )

        assert len(ids) == 5
        rows = s.execute(
            select(SentMessage).order_by(SentMessage.sequence_index)
        ).scalars().all()
        assert len(rows) == 5
        assert [r.status for r in rows] == ["pending"] * 5
        assert [r.tone for r in rows] == ["formal"] * 5
        assert [r.sequence_index for r in rows] == [1, 2, 3, 4, 5]
        # Cadence correctness: row 2 is +7d after row 1, etc.
        deltas = [rows[i].scheduled_for - rows[0].scheduled_for for i in range(5)]
        expected = [timedelta(days=d) for d in [0, 7, 14, 21, 28]]
        for got, want in zip(deltas, expected):
            assert abs((got - want).total_seconds()) < 2  # <2s drift

        # ARQ enqueue_job fired 5 times with the right job_ids
        assert len(redis.enqueued) == 5
        for ent, row_id in zip(redis.enqueued, ids):
            assert ent["fn"] == "send_outreach_email"
            assert ent["kwargs"]["_job_id"] == f"send-{row_id}"

    @pytest.mark.asyncio
    async def test_rejects_wrong_email_count(
        self, session_factory, campaign_output
    ) -> None:
        redis = _FakeRedis()
        s = session_factory()
        with pytest.raises(ValueError, match="expected 5"):
            await outreach_tasks.schedule_outreach_sequence(
                redis=redis,
                session=s,
                campaign_output_id=campaign_output.id,
                sender_user_id="op",
                recipient_email="h@c",
                tone="formal",
                emails=[{"subject": "s", "body": "b"}],
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_cadence(
        self, session_factory, campaign_output
    ) -> None:
        redis = _FakeRedis()
        s = session_factory()
        emails = [{"subject": f"s{i}", "body": "b"} for i in range(5)]
        with pytest.raises(ValueError, match="length-5 starting with 0"):
            await outreach_tasks.schedule_outreach_sequence(
                redis=redis, session=s,
                campaign_output_id=campaign_output.id,
                sender_user_id="op", recipient_email="h@c",
                tone="formal", emails=emails,
                cadence_days=[1, 8, 15, 22, 29],  # doesn't start at 0
            )


# ── send_outreach_email ─────────────────────────────────────────────


class TestSendOutreachEmail:

    @pytest.mark.asyncio
    async def test_dry_run_happy_path(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default env (dry-run). Task should mark row 'sent' and enqueue
        a poll using the synthetic conversation_id."""
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)

        s = session_factory()
        row = SentMessage(
            campaign_output_id=campaign_output.id,
            sender_user_id="op-1",
            recipient_email="hm@corp.com",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            status="pending",
            subject="Hello",
            body="<p>hi</p>",
        )
        s.add(row)
        s.commit()
        row_id = row.id
        s.close()

        redis = _FakeRedis()
        await outreach_tasks.send_outreach_email({"redis": redis}, row_id)

        s = session_factory()
        reloaded = s.execute(
            select(SentMessage).where(SentMessage.id == row_id)
        ).scalar_one()
        assert reloaded.status == "sent"
        assert reloaded.sent_at is not None
        assert reloaded.graph_message_id and reloaded.graph_message_id.startswith("dryrun-msg-")
        assert reloaded.conversation_id and reloaded.conversation_id.startswith("dryrun-conv-")

        # Poll enqueued
        assert len(redis.enqueued) == 1
        assert redis.enqueued[0]["fn"] == "poll_replies_for_conversation"
        assert redis.enqueued[0]["args"][0] == reloaded.conversation_id

    @pytest.mark.asyncio
    async def test_already_sent_exits_early(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Restart-safety: if the row is already 'sent', task exits
        without re-calling Graph or enqueuing another poll."""
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)

        s = session_factory()
        row = SentMessage(
            campaign_output_id=campaign_output.id,
            sender_user_id="op",
            recipient_email="r@x",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            status="sent",           # <<< already sent
            sent_at=datetime.utcnow(),
            graph_message_id="orig-id",
            conversation_id="orig-conv",
            subject="s", body="b",
        )
        s.add(row)
        s.commit()
        row_id = row.id
        s.close()

        redis = _FakeRedis()
        await outreach_tasks.send_outreach_email({"redis": redis}, row_id)

        # Graph client not called (would be dry-run anyway, but also:
        # no new poll job was enqueued).
        assert redis.enqueued == []

    @pytest.mark.asyncio
    async def test_graph_error_marks_failed(
        self, session_factory, campaign_output,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On a GraphError, the row transitions to 'failed' with the
        error message captured. No poll is enqueued."""
        s = session_factory()
        row = SentMessage(
            campaign_output_id=campaign_output.id,
            sender_user_id="op",
            recipient_email="r@x",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            status="pending",
            subject="s", body="b",
        )
        s.add(row)
        s.commit()
        row_id = row.id
        s.close()

        # Patch GraphClient inside outreach_tasks with one that raises.
        class _FailingGraphClient:
            def __init__(self, **_: Any) -> None: pass
            async def send_mail(self, **_: Any):
                from vacancysoft.outreach import GraphError
                raise GraphError("403 forbidden", status_code=403)

        monkeypatch.setattr(outreach_tasks, "GraphClient", _FailingGraphClient)

        redis = _FakeRedis()
        await outreach_tasks.send_outreach_email({"redis": redis}, row_id)

        s = session_factory()
        reloaded = s.execute(
            select(SentMessage).where(SentMessage.id == row_id)
        ).scalar_one()
        assert reloaded.status == "failed"
        assert reloaded.error_message and "403" in reloaded.error_message
        assert redis.enqueued == []

    @pytest.mark.asyncio
    async def test_missing_row_is_safe_noop(self, session_factory) -> None:
        redis = _FakeRedis()
        # No row created
        await outreach_tasks.send_outreach_email(
            {"redis": redis}, "id-that-does-not-exist"
        )
        assert redis.enqueued == []


# ── poll_replies_for_conversation ──────────────────────────────────


class TestPollReplies:

    def _seed_sequence(
        self,
        session_factory,
        campaign_output,
        *,
        first_sent_at: datetime,
        total: int = 5,
    ) -> list[str]:
        """Helper: insert one 'sent' row + (total-1) 'pending' rows, all
        sharing the same conversation_id. Returns their ids."""
        s = session_factory()
        ids = []
        for i in range(1, total + 1):
            status = "sent" if i == 1 else "pending"
            row = SentMessage(
                campaign_output_id=campaign_output.id,
                sender_user_id="op-1",
                recipient_email="hm@corp.com",
                sequence_index=i,
                tone="formal",
                scheduled_for=first_sent_at + timedelta(days=(i - 1) * 7),
                conversation_id="CONV-1",
                status=status,
                sent_at=first_sent_at if i == 1 else None,
                graph_message_id=f"out-{i}" if i == 1 else None,
                subject=f"Seq {i}", body="b",
                arq_job_id=f"send-arq-{i}",
            )
            s.add(row)
            s.flush()
            ids.append(row.id)
        s.commit()
        s.close()
        return ids

    @pytest.mark.asyncio
    async def test_no_replies_reenqueues_self(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run list_replies returns [] → poll re-enqueues itself."""
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)
        self._seed_sequence(
            session_factory, campaign_output,
            first_sent_at=datetime.utcnow() - timedelta(hours=1),
        )

        redis = _FakeRedis()
        await outreach_tasks.poll_replies_for_conversation(
            {"redis": redis}, "CONV-1", "op-1"
        )

        assert len(redis.enqueued) == 1
        assert redis.enqueued[0]["fn"] == "poll_replies_for_conversation"

    @pytest.mark.asyncio
    async def test_reply_found_cancels_pending_sequence(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When Graph returns a reply, all remaining pending rows flip
        to cancelled_replied + their ARQ jobs are aborted."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        row_ids = self._seed_sequence(
            session_factory, campaign_output,
            first_sent_at=datetime.utcnow() - timedelta(hours=1),
        )

        # Patch GraphClient to return a reply
        reply = {
            "graph_message_id": "inbound-1",
            "conversation_id": "CONV-1",
            "from_email": "hm@corp.com",
            "received_at": "2026-04-21T14:32:00Z",
            "subject": "Re: Hello",
        }
        fake_graph = _FakeGraphClient(list_result=[reply])
        monkeypatch.setattr(outreach_tasks, "GraphClient", lambda **_: fake_graph)

        redis = _FakeRedis()
        await outreach_tasks.poll_replies_for_conversation(
            {"redis": redis}, "CONV-1", "op-1"
        )

        # 1. All 4 pending rows aborted + flipped to cancelled_replied
        s = session_factory()
        rows = s.execute(
            select(SentMessage).order_by(SentMessage.sequence_index)
        ).scalars().all()
        assert rows[0].status == "sent"  # unchanged
        for r in rows[1:]:
            assert r.status == "cancelled_replied"

        assert len(redis.aborted) == 4

        # 2. Received reply persisted
        replies = s.execute(select(ReceivedReply)).scalars().all()
        assert len(replies) == 1
        assert replies[0].conversation_id == "CONV-1"
        assert replies[0].graph_message_id == "inbound-1"
        assert replies[0].from_email == "hm@corp.com"
        assert replies[0].matched_sent_message_id == rows[1].id  # earliest pending

        # 3. Polling does NOT re-enqueue (reply landed)
        assert all(e["fn"] != "poll_replies_for_conversation" for e in redis.enqueued)

    @pytest.mark.asyncio
    async def test_self_reply_is_ignored(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 'reply' from the sender's own address (e.g. sent-items
        echo) should be filtered out, not trigger cancellation."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        self._seed_sequence(
            session_factory, campaign_output,
            first_sent_at=datetime.utcnow() - timedelta(hours=1),
        )

        self_reply = {
            "graph_message_id": "self-1",
            "conversation_id": "CONV-1",
            "from_email": "op-1",
            "received_at": "2026-04-21T14:32:00Z",
            "subject": "Re: Hello",
        }
        fake_graph = _FakeGraphClient(list_result=[self_reply])
        monkeypatch.setattr(outreach_tasks, "GraphClient", lambda **_: fake_graph)

        redis = _FakeRedis()
        await outreach_tasks.poll_replies_for_conversation(
            {"redis": redis}, "CONV-1", "op-1"
        )

        s = session_factory()
        # No replies stored, no aborts, sequence still pending
        assert s.execute(select(ReceivedReply)).first() is None
        assert redis.aborted == []
        # Poll re-enqueued
        assert any(
            e["fn"] == "poll_replies_for_conversation" for e in redis.enqueued
        )

    @pytest.mark.asyncio
    async def test_stops_polling_after_max_days(
        self, session_factory, campaign_output, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A conversation older than poll_max_days should not re-enqueue."""
        # default is 90 days; seed first-sent at 100 days ago
        self._seed_sequence(
            session_factory, campaign_output,
            first_sent_at=datetime.utcnow() - timedelta(days=100),
        )

        redis = _FakeRedis()
        await outreach_tasks.poll_replies_for_conversation(
            {"redis": redis}, "CONV-1", "op-1"
        )

        assert redis.enqueued == []


# ── cancel_pending_sequence_manual ─────────────────────────────────


class TestCancelManual:

    @pytest.mark.asyncio
    async def test_cancels_only_pending_rows(
        self, session_factory, campaign_output
    ) -> None:
        s = session_factory()
        for i, status in enumerate(["sent", "pending", "pending", "pending"], start=1):
            s.add(SentMessage(
                campaign_output_id=campaign_output.id,
                sender_user_id="op", recipient_email="r@x",
                sequence_index=i, tone="formal",
                scheduled_for=datetime.utcnow(),
                status=status,
                sent_at=datetime.utcnow() if status == "sent" else None,
                subject=f"s{i}", body="b",
                arq_job_id=f"arq-{i}",
            ))
        s.commit()

        redis = _FakeRedis()
        count = await outreach_tasks.cancel_pending_sequence_manual(
            session=s, redis=redis, campaign_output_id=campaign_output.id,
        )

        assert count == 3  # three pending rows
        assert set(redis.aborted) == {"arq-2", "arq-3", "arq-4"}

        rows = s.execute(
            select(SentMessage).order_by(SentMessage.sequence_index)
        ).scalars().all()
        statuses = [r.status for r in rows]
        assert statuses == ["sent", "cancelled_manual", "cancelled_manual", "cancelled_manual"]
