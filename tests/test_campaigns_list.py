"""Tests for the GET /api/campaigns list, /detail, and /launchers endpoints (PR P8).

Pattern matches test_campaigns_launch.py — bare FastAPI app with just
the campaigns router mounted, in-memory SQLite via StaticPool so the
schema is visible across the TestClient's worker thread.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vacancysoft.api.routes import campaigns as campaigns_route
from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    ClassificationResult,
    ClickEvent,
    EnrichedJob,
    IntelligenceDossier,
    OpenEvent,
    RawJob,
    ReceivedReply,
    SentMessage,
    Source,
    User,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine, monkeypatch: pytest.MonkeyPatch):
    Factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    from vacancysoft.db import engine as engine_mod
    monkeypatch.setattr(engine_mod, "SessionLocal", Factory)
    monkeypatch.setattr(campaigns_route, "SessionLocal", Factory)
    return Factory


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(campaigns_route.router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _build_lead(s, *, employer="ExampleCorp", title="VP Eng",
                category="Risk", hm_email="hm@target.com", hm_name="HM Name"):
    """Source → RawJob → EnrichedJob → Dossier → CampaignOutput. Returns the
    CampaignOutput. Optional category writes a ClassificationResult."""
    src = Source(
        source_key=f"src-{uuid4().hex[:6]}",
        employer_name=employer,
        base_url="https://example.com/jobs",
        hostname="example.com",
        source_type="ats_html",
        adapter_name="generic_site",
        active=True,
        seed_type="manual",
        fingerprint=f"fp-{uuid4().hex[:8]}",
        capability_blob={},
    )
    s.add(src); s.flush()
    raw = RawJob(
        id=str(uuid4()),
        source_id=src.id,
        source_run_id=str(uuid4()),
        extraction_attempt_id=str(uuid4()),
        title_raw=title,
        job_fingerprint=f"jfp-{uuid4().hex[:8]}",
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        discovery_ts=datetime.utcnow(),
        completeness_score=1.0,
        extraction_confidence=1.0,
        is_deleted_at_source=False,
        provenance_blob={},
    )
    s.add(raw); s.flush()
    ej = EnrichedJob(
        raw_job_id=raw.id,
        canonical_job_key=f"key-{uuid4().hex[:8]}",
        title=title,
        team=employer,
        location_city="London",
        location_country="UK",
    )
    s.add(ej); s.flush()
    if category:
        s.add(ClassificationResult(
            enriched_job_id=ej.id,
            classifier_version="v1",
            taxonomy_version="v1",
            primary_taxonomy_key=category,
            decision="accept",
        ))
    dossier = IntelligenceDossier(
        enriched_job_id=ej.id,
        prompt_version="v1",
        category_used=category or "",
        model_used="gpt-test",
        hiring_managers=[
            {"name": hm_name, "email": hm_email, "confidence": 0.9},
        ],
    )
    s.add(dossier); s.flush()
    co = CampaignOutput(
        dossier_id=dossier.id,
        model_used="gpt-test",
        outreach_emails={"emails": []},
    )
    s.add(co); s.flush()
    return co


def _add_sequence(s, *, campaign_output_id, sender_user_id, recipient_email,
                  statuses, conversation_id="conv-1"):
    """Insert 5 SentMessage rows with the given statuses (length-5)."""
    assert len(statuses) == 5
    base = datetime.utcnow() - timedelta(hours=1)
    for i, status in enumerate(statuses, start=1):
        s.add(SentMessage(
            campaign_output_id=campaign_output_id,
            sender_user_id=sender_user_id,
            recipient_email=recipient_email,
            sequence_index=i,
            tone="formal",
            scheduled_for=base + timedelta(days=i - 1),
            sent_at=base + timedelta(days=i - 1) if status in ("sent", "cancelled_replied") else None,
            graph_message_id=f"g-{uuid4().hex[:6]}" if status == "sent" else None,
            conversation_id=conversation_id,
            status=status,
            subject=f"Step {i}",
            body=f"<p>body {i}</p>",
            arq_job_id=f"arq-{uuid4().hex[:6]}",
        ))
    s.commit()


def _add_user(s, *, oid, email, display_name):
    u = User(
        entra_object_id=oid, email=email, display_name=display_name,
        role="operator", active=True,
    )
    s.add(u); s.commit()
    return u


# ── /api/campaigns ─────────────────────────────────────────────────


class TestListCampaigns:

    def test_empty_returns_empty_list(self, client, session_factory):
        res = client.get("/api/campaigns")
        assert res.status_code == 200
        body = res.json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_basic_row_shape(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s, employer="Goldman Sachs", title="VP Risk",
                         category="Risk", hm_email="sarah@gs.com", hm_name="Sarah Chen")
        _add_user(s, oid="oid-alice", email="alice@bs.com", display_name="Alice")
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="oid-alice",
            recipient_email="sarah@gs.com",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )

        res = client.get("/api/campaigns")
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["company"] == "Goldman Sachs"
        assert item["title"] == "VP Risk"
        assert item["category"] == "Risk"
        assert item["location_city"] == "London"
        assert item["location_country"] == "UK"
        assert item["hiring_manager"] == {"email": "sarah@gs.com", "name": "Sarah Chen"}
        assert item["sender"]["display_name"] == "Alice"
        assert item["sender"]["email"] == "alice@bs.com"
        assert item["status"] == "sent"
        assert item["stage"] == {"sent": 1, "pending": 4, "cancelled": 0, "failed": 0, "total": 5}
        assert item["counts"] == {"opens": 0, "clicks": 0, "replies": 0}

    def test_status_replied(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s)
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="op",
            recipient_email="hm@target.com",
            statuses=["sent", "cancelled_replied", "cancelled_replied", "cancelled_replied", "cancelled_replied"],
            conversation_id="C1",
        )
        s.add(ReceivedReply(
            conversation_id="C1", sender_user_id="op",
            graph_message_id="reply-1", from_email="hm@target.com",
            received_at=datetime.utcnow(), subject="Re: Hi",
        ))
        s.commit()

        res = client.get("/api/campaigns")
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "replied"
        assert items[0]["counts"]["replies"] == 1

    def test_status_opened(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s)
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="op",
            recipient_email="hm@x.com",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )
        sm = s.query(SentMessage).first()
        s.add(OpenEvent(
            sent_message_id=sm.id, opened_at=datetime.utcnow(),
            user_agent="Chrome", likely_apple_mpp=False,
        ))
        s.commit()

        res = client.get("/api/campaigns")
        items = res.json()["items"]
        assert items[0]["status"] == "opened"
        assert items[0]["counts"]["opens"] == 1

    def test_mpp_opens_excluded_from_list_count(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s)
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="op",
            recipient_email="hm@x.com",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )
        sm = s.query(SentMessage).first()
        s.add(OpenEvent(sent_message_id=sm.id, opened_at=datetime.utcnow(),
                        user_agent="GoogleImageProxy", likely_apple_mpp=True))
        s.add(OpenEvent(sent_message_id=sm.id, opened_at=datetime.utcnow() + timedelta(seconds=70),
                        user_agent="Chrome", likely_apple_mpp=False))
        s.commit()

        res = client.get("/api/campaigns")
        items = res.json()["items"]
        assert items[0]["counts"]["opens"] == 1, "MPP-flagged event should be excluded"

    def test_scanner_clicks_excluded_from_list_count(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s)
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="op",
            recipient_email="hm@x.com",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )
        sm = s.query(SentMessage).first()
        s.add(ClickEvent(sent_message_id=sm.id, original_url="https://x.com",
                         clicked_at=datetime.utcnow(),
                         user_agent="Mimecast", likely_scanner=True))
        s.add(ClickEvent(sent_message_id=sm.id, original_url="https://y.com",
                         clicked_at=datetime.utcnow(),
                         user_agent="Chrome", likely_scanner=False))
        s.commit()

        res = client.get("/api/campaigns")
        items = res.json()["items"]
        assert items[0]["counts"]["clicks"] == 1

    def test_filter_by_status(self, client, session_factory):
        s = session_factory()
        co1 = _build_lead(s, employer="Replied Co")
        _add_sequence(
            s, campaign_output_id=co1.id, sender_user_id="op",
            recipient_email="r@a", conversation_id="C1",
            statuses=["sent", "cancelled_replied", "cancelled_replied", "cancelled_replied", "cancelled_replied"],
        )
        s.add(ReceivedReply(
            conversation_id="C1", sender_user_id="op",
            graph_message_id="r1", from_email="r@a",
            received_at=datetime.utcnow(),
        ))
        co2 = _build_lead(s, employer="Pending Co")
        _add_sequence(
            s, campaign_output_id=co2.id, sender_user_id="op",
            recipient_email="r@b",
            statuses=["pending", "pending", "pending", "pending", "pending"],
        )
        s.commit()

        res = client.get("/api/campaigns?status=replied")
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["company"] == "Replied Co"

    def test_filter_by_owner(self, client, session_factory):
        s = session_factory()
        co1 = _build_lead(s, employer="Alice's Co")
        _add_sequence(
            s, campaign_output_id=co1.id, sender_user_id="oid-alice",
            recipient_email="r@a",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )
        co2 = _build_lead(s, employer="Bob's Co")
        _add_sequence(
            s, campaign_output_id=co2.id, sender_user_id="oid-bob",
            recipient_email="r@b",
            statuses=["sent", "pending", "pending", "pending", "pending"],
        )

        res = client.get("/api/campaigns?owner=oid-alice")
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["company"] == "Alice's Co"

    def test_invalid_status_rejected(self, client, session_factory):
        res = client.get("/api/campaigns?status=screamy")
        assert res.status_code == 422

    def test_pagination(self, client, session_factory):
        s = session_factory()
        for i in range(5):
            co = _build_lead(s, employer=f"Co {i}")
            _add_sequence(
                s, campaign_output_id=co.id, sender_user_id="op",
                recipient_email=f"r{i}@x",
                statuses=["sent", "pending", "pending", "pending", "pending"],
                conversation_id=f"conv-{i}",
            )

        res = client.get("/api/campaigns?limit=2&offset=0")
        body = res.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 0

        res2 = client.get("/api/campaigns?limit=2&offset=4")
        assert len(res2.json()["items"]) == 1


# ── /api/campaigns/launchers ──────────────────────────────────────


class TestLaunchers:

    def test_empty(self, client, session_factory):
        res = client.get("/api/campaigns/launchers")
        assert res.status_code == 200
        assert res.json() == {"launchers": []}

    def test_distinct_with_user_resolution(self, client, session_factory):
        s = session_factory()
        _add_user(s, oid="oid-alice", email="alice@bs.com", display_name="Alice")
        _add_user(s, oid="oid-bob", email="bob@bs.com", display_name="Bob")
        for sender_id, n in [("oid-alice", 2), ("oid-bob", 1), ("oid-orphan", 1)]:
            for _ in range(n):
                co = _build_lead(s)
                _add_sequence(
                    s, campaign_output_id=co.id, sender_user_id=sender_id,
                    recipient_email="r@x",
                    statuses=["sent", "pending", "pending", "pending", "pending"],
                    conversation_id=f"conv-{uuid4().hex[:6]}",
                )

        res = client.get("/api/campaigns/launchers")
        launchers = res.json()["launchers"]
        assert len(launchers) == 3
        # Alice has 2, sorted first
        assert launchers[0]["sender_user_id"] == "oid-alice"
        assert launchers[0]["display_name"] == "Alice"
        assert launchers[0]["campaign_count"] == 2
        # Orphan has no user row
        orphan = next(l for l in launchers if l["sender_user_id"] == "oid-orphan")
        assert orphan["display_name"] is None


# ── /api/campaigns/{id}/detail ────────────────────────────────────


class TestCampaignDetail:

    def test_404_on_missing(self, client, session_factory):
        res = client.get("/api/campaigns/nope/detail")
        assert res.status_code == 404

    def test_full_detail_with_events(self, client, session_factory):
        s = session_factory()
        co = _build_lead(s, employer="Goldman", title="VP Risk",
                         hm_email="sarah@gs.com", hm_name="Sarah Chen")
        _add_user(s, oid="oid-alice", email="alice@bs.com", display_name="Alice")
        _add_sequence(
            s, campaign_output_id=co.id, sender_user_id="oid-alice",
            recipient_email="sarah@gs.com",
            statuses=["sent", "sent", "pending", "pending", "pending"],
            conversation_id="C1",
        )
        sms = list(s.query(SentMessage).order_by(SentMessage.sequence_index))
        # Two opens on step 1 — one MPP, one not.
        s.add(OpenEvent(sent_message_id=sms[0].id, opened_at=datetime.utcnow(),
                        user_agent="GoogleImageProxy", likely_apple_mpp=True))
        s.add(OpenEvent(sent_message_id=sms[0].id, opened_at=datetime.utcnow() + timedelta(seconds=70),
                        user_agent="Chrome", likely_apple_mpp=False))
        # One scanner click on step 1.
        s.add(ClickEvent(sent_message_id=sms[0].id, original_url="https://example.com",
                         clicked_at=datetime.utcnow(),
                         user_agent="Mimecast", likely_scanner=True))
        # One reply on the conversation.
        s.add(ReceivedReply(conversation_id="C1", sender_user_id="oid-alice",
                            graph_message_id="r1", from_email="sarah@gs.com",
                            received_at=datetime.utcnow(), subject="Re: VP Risk"))
        s.commit()

        res = client.get(f"/api/campaigns/{co.id}/detail")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["company"] == "Goldman"
        assert body["title"] == "VP Risk"
        assert body["hiring_manager"] == {"email": "sarah@gs.com", "name": "Sarah Chen"}
        assert body["sender"]["display_name"] == "Alice"
        assert body["status"] == "replied"
        # Counts mirror the list view (excludes MPP + scanner)
        assert body["counts"]["opens"] == 1
        assert body["counts"]["clicks"] == 0
        assert body["counts"]["replies"] == 1
        # Steps: 5 in sequence order
        assert len(body["steps"]) == 5
        step1 = body["steps"][0]
        assert step1["sequence_index"] == 1
        assert step1["status"] == "sent"
        # Detail INCLUDES flagged events (UI greys them out)
        assert len(step1["opens"]) == 2
        assert any(o["likely_apple_mpp"] for o in step1["opens"])
        assert len(step1["clicks"]) == 1
        assert step1["clicks"][0]["likely_scanner"] is True
        # Replies block
        assert len(body["replies"]) == 1
        assert body["replies"][0]["from_email"] == "sarah@gs.com"
