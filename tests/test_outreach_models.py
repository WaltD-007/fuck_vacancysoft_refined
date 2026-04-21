"""Tests for the SentMessage + ReceivedReply models (migration 0008).

Uses an in-memory SQLite DB with Base.metadata.create_all (same pattern
as test_persistence_failure.py and test_paste_lead.py) rather than
running Alembic — SQLite is enough to verify the model definitions are
self-consistent, FK relationships resolve, defaults fire, and indexes
don't collide. The migration itself is a straight op.create_table +
op.create_index sequence whose correctness mirrors the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    ReceivedReply,
    SentMessage,
)


@pytest.fixture
def session():
    """In-memory SQLite DB. PRAGMA foreign_keys=OFF by default, so tests
    don't need a full upstream row chain; the one FK-enforcement test
    turns it on inline."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def _make_campaign_output(session) -> CampaignOutput:
    """Cheap stand-in for a CampaignOutput row. Avoids creating the full
    upstream Source/RawJob/EnrichedJob/Dossier chain — none of these
    tests care about upstream integrity, only about outreach columns.
    SQLite default FK-disabled means the placeholder dossier_id is fine."""
    campaign = CampaignOutput(dossier_id=f"stub-{uuid4().hex[:8]}", model_used="gpt-5.4")
    session.add(campaign)
    session.flush()
    return campaign


class TestSentMessageModel:

    def test_round_trip(self, session) -> None:
        campaign = _make_campaign_output(session)
        msg = SentMessage(
            campaign_output_id=campaign.id,
            sender_user_id="op-1",
            recipient_email="hm@corp.com",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            status="pending",
            subject="Hello",
            body="<p>hi</p>",
        )
        session.add(msg)
        session.commit()

        loaded = session.execute(
            select(SentMessage).where(SentMessage.id == msg.id)
        ).scalar_one()
        assert loaded.campaign_output_id == campaign.id
        assert loaded.status == "pending"
        assert loaded.sequence_index == 1
        assert loaded.tone == "formal"
        # default timestamps were populated
        assert loaded.created_at is not None
        assert loaded.updated_at is not None

    def test_status_defaults_to_pending(self, session) -> None:
        """If the caller doesn't supply status, default fires."""
        campaign = _make_campaign_output(session)
        msg = SentMessage(
            campaign_output_id=campaign.id,
            sender_user_id="op",
            recipient_email="a@b",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            subject="s",
            body="b",
        )
        session.add(msg)
        session.commit()
        assert msg.status == "pending"

    def test_lifecycle_transitions(self, session) -> None:
        """Simulate the four legal transitions from 'pending'."""
        campaign = _make_campaign_output(session)
        now = datetime.utcnow()

        rows = []
        for i, final_status in enumerate(
            ["sent", "cancelled_replied", "cancelled_manual", "failed"], start=1
        ):
            msg = SentMessage(
                campaign_output_id=campaign.id,
                sender_user_id="op",
                recipient_email=f"r{i}@x",
                sequence_index=i,
                tone="formal",
                scheduled_for=now,
                subject="s",
                body="b",
            )
            session.add(msg)
            rows.append((msg, final_status))
        session.commit()

        for msg, final_status in rows:
            msg.status = final_status
            if final_status == "sent":
                msg.sent_at = now
                msg.graph_message_id = "AAMk="
                msg.conversation_id = "CONV-1"
            elif final_status == "failed":
                msg.error_message = "Graph 403 AccessDenied"
        session.commit()

        statuses = {m.status for m in session.execute(select(SentMessage)).scalars()}
        assert statuses == {"sent", "cancelled_replied", "cancelled_manual", "failed"}

    def test_fk_to_campaign_output_enforced(self, session) -> None:
        """SQLite needs PRAGMA foreign_keys=ON for this to raise. We turn
        it on inline because the rest of the suite doesn't need it."""
        session.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))

        msg = SentMessage(
            campaign_output_id="does-not-exist",
            sender_user_id="op",
            recipient_email="r@x",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            subject="s",
            body="b",
        )
        session.add(msg)
        with pytest.raises(IntegrityError):
            session.commit()


class TestReceivedReplyModel:

    def test_round_trip(self, session) -> None:
        reply = ReceivedReply(
            conversation_id="CONV-1",
            sender_user_id="op-1",
            graph_message_id="reply-AAMk=",
            from_email="hm@corp.com",
            received_at=datetime.utcnow(),
            subject="Re: Hello",
        )
        session.add(reply)
        session.commit()

        loaded = session.execute(
            select(ReceivedReply).where(ReceivedReply.id == reply.id)
        ).scalar_one()
        assert loaded.conversation_id == "CONV-1"
        assert loaded.from_email == "hm@corp.com"
        assert loaded.matched_sent_message_id is None  # nullable

    def test_graph_message_id_unique(self, session) -> None:
        """Same Graph message can't be inserted twice — prevents double-
        counting a reply if the poller runs overlapping passes."""
        a = ReceivedReply(
            conversation_id="C1",
            sender_user_id="op",
            graph_message_id="dup-id",
            from_email="x@y",
            received_at=datetime.utcnow(),
        )
        session.add(a)
        session.commit()

        b = ReceivedReply(
            conversation_id="C2",
            sender_user_id="op",
            graph_message_id="dup-id",  # same id
            from_email="x@y",
            received_at=datetime.utcnow(),
        )
        session.add(b)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_matched_sent_message_fk(self, session) -> None:
        """When the poller identifies which sent_message the reply was
        against, it records the FK."""
        campaign = _make_campaign_output(session)
        sent = SentMessage(
            campaign_output_id=campaign.id,
            sender_user_id="op",
            recipient_email="hm@c",
            sequence_index=1,
            tone="formal",
            scheduled_for=datetime.utcnow(),
            sent_at=datetime.utcnow(),
            conversation_id="CONV-1",
            graph_message_id="out-1",
            status="sent",
            subject="s",
            body="b",
        )
        session.add(sent)
        session.commit()

        reply = ReceivedReply(
            conversation_id="CONV-1",
            sender_user_id="op",
            graph_message_id="in-1",
            from_email="hm@c",
            received_at=datetime.utcnow() + timedelta(hours=2),
            matched_sent_message_id=sent.id,
        )
        session.add(reply)
        session.commit()

        assert reply.matched_sent_message_id == sent.id


class TestCrossQuery:

    def test_find_pending_sends_by_conversation(self, session) -> None:
        """The cancel-on-reply path queries for all pending sends in a
        conversation. This test verifies the index-friendly shape of
        that query returns what we expect."""
        campaign = _make_campaign_output(session)
        now = datetime.utcnow()

        # 5 sent_messages in one conversation, sequence 1 sent, 2-5 pending
        for i in range(1, 6):
            msg = SentMessage(
                campaign_output_id=campaign.id,
                sender_user_id="op",
                recipient_email="hm@c",
                sequence_index=i,
                tone="formal",
                scheduled_for=now + timedelta(days=(i - 1) * 7),
                conversation_id="CONV-1",
                status="sent" if i == 1 else "pending",
                sent_at=now if i == 1 else None,
                graph_message_id=f"out-{i}" if i == 1 else None,
                subject=f"Seq {i}",
                body=f"Body {i}",
            )
            session.add(msg)
        session.commit()

        pending_in_conv = session.execute(
            select(SentMessage).where(
                SentMessage.conversation_id == "CONV-1",
                SentMessage.status == "pending",
            ).order_by(SentMessage.sequence_index)
        ).scalars().all()

        assert len(pending_in_conv) == 4
        assert [m.sequence_index for m in pending_in_conv] == [2, 3, 4, 5]
