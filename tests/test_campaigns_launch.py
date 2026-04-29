"""Tests for the /api/campaigns/{id}/launch and /cancel endpoints.

Canary delta: these are the API entry points that wrap the worker
helpers (``schedule_outreach_sequence`` / ``cancel_pending_sequence_manual``).
The worker helpers themselves are exercised in test_outreach_tasks.py;
here we cover the request/response shape, validation, identity
resolution, recipient fallback, and the redis-pool plumbing.

Tests run with FastAPI's TestClient against an in-memory SQLite DB and
a fake redis pool. No real Graph calls — the worker helper invoked
under the hood is the same dry-run aware code path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vacancysoft.api.routes import campaigns as campaigns_route
from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    EnrichedJob,
    IntelligenceDossier,
    RawJob,
    SentMessage,
    Source,
    User,
)
from vacancysoft.worker import outreach_tasks


# ── Fakes ───────────────────────────────────────────────────────────


class _FakeRedisJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _FakeRedis:
    """Mirrors the helper used in test_outreach_tasks.py."""

    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self.aborted: list[str] = []

    async def enqueue_job(self, fn_name: str, *args: Any, **kwargs: Any):
        self.enqueued.append({"fn": fn_name, "args": args, "kwargs": kwargs})
        return _FakeRedisJob(job_id=kwargs.get("_job_id", f"arq-{len(self.enqueued)}"))

    async def abort_job(self, job_id: str) -> None:
        self.aborted.append(job_id)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_engine():
    """In-memory SQLite shared across threads.

    FastAPI's TestClient runs request handlers in a worker thread; the
    fixture's setup runs in the main thread. Default ``sqlite:///:memory:``
    creates a per-connection DB and disallows cross-thread sharing — both
    of which break us. ``StaticPool`` + ``check_same_thread=False`` keeps
    one connection alive for the engine's lifetime and reuses it
    everywhere, so the schema created here is visible to the handler.
    """
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
    """Patch SessionLocal in BOTH the route module and the worker module
    so all session opens go through our test factory. The endpoint code
    in routes/campaigns.py opens its own SessionLocal context; the
    worker helper it calls also opens its own. They share the same
    engine via this factory."""
    Factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    # The routes module imports SessionLocal at function-call time
    # (inside the handler), so we have to patch the source module.
    from vacancysoft.db import engine as engine_mod
    monkeypatch.setattr(engine_mod, "SessionLocal", Factory)
    monkeypatch.setattr(campaigns_route, "SessionLocal", Factory)
    monkeypatch.setattr(outreach_tasks, "SessionLocal", Factory)
    return Factory


@pytest.fixture
def operator(session_factory) -> User:
    """A single active User so get_current_user resolves via single-user-mode
    fallback (no header required)."""
    s = session_factory()
    user = User(
        entra_object_id="e3a5d0e2-7c92-4cab-aaaa-1111111111aa",
        email="alice@example.com",
        display_name="Alice Operator",
        role="operator",
        active=True,
    )
    s.add(user)
    s.commit()
    s.refresh(user)
    s.close()
    return user


@pytest.fixture
def campaign_with_dossier(session_factory) -> tuple[str, str]:
    """Create the full chain Source → RawJob → EnrichedJob → Dossier →
    CampaignOutput so /launch has something to launch against. Returns
    (campaign_output_id, dossier_hm_email)."""
    s = session_factory()

    src = Source(
        source_key=f"src-{uuid4().hex[:6]}",
        employer_name="ExampleCorp",
        base_url="https://example.com/jobs",
        hostname="example.com",
        source_type="ats_html",
        adapter_name="generic_site",
        active=True,
        seed_type="manual",
        fingerprint=f"fp-{uuid4().hex[:8]}",
        capability_blob={},
    )
    s.add(src)
    s.flush()

    raw = RawJob(
        id=str(uuid4()),
        source_id=src.id,
        source_run_id=str(uuid4()),
        extraction_attempt_id=str(uuid4()),
        title_raw="VP Engineering",
        job_fingerprint=f"jfp-{uuid4().hex[:8]}",
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        discovery_ts=datetime.utcnow(),
        completeness_score=1.0,
        extraction_confidence=1.0,
        is_deleted_at_source=False,
        provenance_blob={},
    )
    s.add(raw)
    s.flush()

    ej = EnrichedJob(
        raw_job_id=raw.id,
        canonical_job_key=f"key-{uuid4().hex[:8]}",
        title="VP Engineering",
        team="ExampleCorp",
        location_text="London",
    )
    s.add(ej)
    s.flush()

    dossier = IntelligenceDossier(
        enriched_job_id=ej.id,
        prompt_version="v1",
        category_used="Technology",
        model_used="gpt-test",
        hiring_managers=[
            {"name": "Bob HM", "email": "bob.hm@example.com", "confidence": 0.9},
        ],
    )
    s.add(dossier)
    s.flush()

    # Six-tone campaign — minimum viable for the launch endpoint to
    # extract a length-5 sequence for any tone.
    co = CampaignOutput(
        dossier_id=dossier.id,
        model_used="gpt-test",
        outreach_emails={
            "emails": [
                {
                    "sequence": i + 1,
                    "variants": {
                        tone: {"subject": f"S{i+1} {tone}", "body": f"<p>B{i+1} {tone}</p>"}
                        for tone in (
                            "formal", "informal", "consultative",
                            "direct", "candidate_spec", "technical",
                        )
                    },
                }
                for i in range(5)
            ]
        },
    )
    s.add(co)
    s.commit()
    co_id = co.id
    s.close()
    return co_id, "bob.hm@example.com"


@pytest.fixture
def app(session_factory):
    """Bare FastAPI app with just the campaigns router mounted, plus
    a fake redis on app.state.redis. Avoids spinning up the full server
    (which has DB / Redis / scheduling side-effects we don't need)."""
    fake_redis = _FakeRedis()
    application = FastAPI()
    application.include_router(campaigns_route.router)
    application.state.redis = fake_redis
    application.state._test_fake_redis = fake_redis  # for assertions
    return application


@pytest.fixture
def client(app, monkeypatch: pytest.MonkeyPatch):
    # Dry-run by default — no real Graph calls anywhere.
    monkeypatch.setenv("OUTREACH_DRY_RUN", "true")
    return TestClient(app)


# ── /launch ─────────────────────────────────────────────────────────


class TestLaunchCampaign:

    def test_happy_path_uses_dossier_recipient(
        self, client, session_factory, app, operator, campaign_with_dossier
    ):
        co_id, hm_email = campaign_with_dossier

        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal"},
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "scheduled"
        assert len(body["sent_message_ids"]) == 5
        assert body["first_send_scheduled_for"]

        # All 5 SentMessage rows live, point at the right recipient,
        # status pending, tone formal, in sequence order.
        s = session_factory()
        rows = s.execute(
            select(SentMessage).order_by(SentMessage.sequence_index)
        ).scalars().all()
        assert len(rows) == 5
        assert {r.recipient_email for r in rows} == {hm_email}
        assert {r.tone for r in rows} == {"formal"}
        assert [r.status for r in rows] == ["pending"] * 5
        # sender_user_id should be the operator's Entra OID
        assert {r.sender_user_id for r in rows} == {operator.entra_object_id}
        s.close()

        # Redis got 5 enqueue_job calls
        fake_redis = app.state._test_fake_redis
        assert len(fake_redis.enqueued) == 5
        assert all(e["fn"] == "send_outreach_email" for e in fake_redis.enqueued)

    def test_recipient_override(
        self, client, session_factory, operator, campaign_with_dossier
    ):
        co_id, _hm = campaign_with_dossier
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal", "recipient_email": "override@target.com"},
        )
        assert res.status_code == 200, res.text

        s = session_factory()
        rows = s.execute(select(SentMessage)).scalars().all()
        assert {r.recipient_email for r in rows} == {"override@target.com"}
        s.close()

    def test_custom_cadence(
        self, client, session_factory, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal", "cadence_days": [0, 1, 2, 3, 4]},
        )
        assert res.status_code == 200, res.text

        s = session_factory()
        rows = s.execute(
            select(SentMessage).order_by(SentMessage.sequence_index)
        ).scalars().all()
        deltas = [
            (r.scheduled_for - rows[0].scheduled_for).total_seconds() / 86400.0
            for r in rows
        ]
        # Allow a small drift; sub-second precision isn't relevant.
        for got, want in zip(deltas, [0, 1, 2, 3, 4]):
            assert abs(got - want) < 0.01
        s.close()

    def test_404_when_campaign_missing(
        self, client, operator
    ):
        res = client.post(
            "/api/campaigns/nope-not-real/launch",
            json={"tone": "formal"},
        )
        assert res.status_code == 404

    def test_422_on_invalid_tone(
        self, client, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "screamy"},
        )
        assert res.status_code == 422
        assert "invalid tone" in res.text

    def test_422_on_invalid_cadence(
        self, client, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        # cadence doesn't start with 0
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal", "cadence_days": [1, 8, 15, 22, 29]},
        )
        assert res.status_code == 422

        # cadence not length-5
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal", "cadence_days": [0, 7, 14]},
        )
        assert res.status_code == 422

    def test_422_when_no_recipient_resolvable(
        self, client, session_factory, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        # Wipe HMs from the dossier so the fallback path has nothing.
        s = session_factory()
        co = s.execute(
            select(CampaignOutput).where(CampaignOutput.id == co_id)
        ).scalar_one()
        dossier = s.execute(
            select(IntelligenceDossier).where(IntelligenceDossier.id == co.dossier_id)
        ).scalar_one()
        dossier.hiring_managers = []
        s.commit()
        s.close()

        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal"},  # no recipient_email
        )
        assert res.status_code == 422
        assert "recipient" in res.text.lower()

    def test_503_when_no_redis_pool(
        self, app, session_factory, operator, campaign_with_dossier,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If app.state.redis is None (worker not running), launch
        returns 503 cleanly rather than half-writing the sequence."""
        co_id, _ = campaign_with_dossier
        app.state.redis = None
        try:
            monkeypatch.setenv("OUTREACH_DRY_RUN", "true")
            with TestClient(app) as c:
                res = c.post(
                    f"/api/campaigns/{co_id}/launch",
                    json={"tone": "formal"},
                )
            assert res.status_code == 503
            # No SentMessage rows leaked.
            s = session_factory()
            assert s.execute(select(SentMessage)).first() is None
            s.close()
        finally:
            app.state.redis = app.state._test_fake_redis


# ── /cancel ─────────────────────────────────────────────────────────


class TestCancelCampaign:

    def test_cancel_after_launch(
        self, client, session_factory, app, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        # Launch first
        res = client.post(
            f"/api/campaigns/{co_id}/launch",
            json={"tone": "formal"},
        )
        assert res.status_code == 200

        # Now cancel — all 5 still pending in dry-run since the worker
        # hasn't fired (we're calling endpoints synchronously).
        res = client.post(f"/api/campaigns/{co_id}/cancel")
        assert res.status_code == 200, res.text
        assert res.json() == {"cancelled_count": 5}

        s = session_factory()
        rows = s.execute(select(SentMessage)).scalars().all()
        assert {r.status for r in rows} == {"cancelled_manual"}
        s.close()

        # ARQ aborts fired for each row's job_id
        fake_redis = app.state._test_fake_redis
        assert len(fake_redis.aborted) == 5

    def test_cancel_idempotent_returns_zero(
        self, client, operator, campaign_with_dossier
    ):
        co_id, _ = campaign_with_dossier
        client.post(f"/api/campaigns/{co_id}/launch", json={"tone": "formal"})
        client.post(f"/api/campaigns/{co_id}/cancel")
        # Second cancel — nothing is pending anymore
        res = client.post(f"/api/campaigns/{co_id}/cancel")
        assert res.status_code == 200
        assert res.json() == {"cancelled_count": 0}

    def test_404_when_campaign_missing(self, client, operator):
        res = client.post("/api/campaigns/nope-not-real/cancel")
        assert res.status_code == 404
