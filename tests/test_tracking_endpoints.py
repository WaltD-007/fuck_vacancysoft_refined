"""Tests for /t/o/{token} and /t/c/{token} endpoints.

Pattern matches test_campaigns_launch.py — bare FastAPI app with just
the tracking router mounted, in-memory SQLite via StaticPool so the
schema is visible across the TestClient's worker thread.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vacancysoft.api.routes import tracking as tracking_route
from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    ClickEvent,
    OpenEvent,
    SentMessage,
)
from vacancysoft.outreach import tracking as tk


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file needs a deterministic tracking secret —
    apply once via autouse so individual tests don't have to repeat."""
    monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "test-secret-fixed")


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
    monkeypatch.setattr(tracking_route, "SessionLocal", Factory)
    return Factory


@pytest.fixture
def sent_message(session_factory) -> SentMessage:
    """One SentMessage with status='sent' so endpoints have a real row
    to FK against. campaign_output is required by the FK."""
    s = session_factory()
    co = CampaignOutput(dossier_id=f"d-{uuid4().hex[:6]}", model_used="gpt-test")
    s.add(co)
    s.flush()
    sm = SentMessage(
        campaign_output_id=co.id,
        sender_user_id="op-1",
        recipient_email="hm@target.com",
        sequence_index=1,
        tone="formal",
        scheduled_for=datetime.utcnow() - timedelta(hours=1),
        sent_at=datetime.utcnow() - timedelta(hours=1),
        status="sent",
        graph_message_id="graph-1",
        conversation_id="conv-1",
        subject="Hi",
        body="<p>x</p>",
    )
    s.add(sm)
    s.commit()
    s.refresh(sm)
    s.close()
    return sm


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(tracking_route.router)
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ── /t/o/{token} ────────────────────────────────────────────────────


class TestOpenEndpoint:

    def test_happy_path_records_event_and_returns_pixel(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "o")
        res = client.get(f"/t/o/{token}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/gif"
        assert res.content[:6] == b"GIF89a"

        s = session_factory()
        events = s.execute(select(OpenEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].sent_message_id == sent_message.id
        s.close()

    def test_bad_token_returns_204_no_event(
        self, client, session_factory,
    ):
        res = client.get("/t/o/garbage-token")
        assert res.status_code == 204

        s = session_factory()
        assert s.execute(select(OpenEvent)).first() is None
        s.close()

    def test_missing_sent_message_still_returns_pixel_no_event(
        self, client, session_factory,
    ):
        """Token signs a sent_message_id that doesn't exist. We still
        return the pixel (recipient sees nothing weird) but don't write
        a dangling event row."""
        token = tk.sign_token("does-not-exist", "o")
        res = client.get(f"/t/o/{token}")
        assert res.status_code == 200
        assert res.content[:6] == b"GIF89a"

        s = session_factory()
        assert s.execute(select(OpenEvent)).first() is None
        s.close()

    def test_dedupe_within_window(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "o")
        client.get(f"/t/o/{token}")
        client.get(f"/t/o/{token}")
        client.get(f"/t/o/{token}")

        s = session_factory()
        events = s.execute(select(OpenEvent)).scalars().all()
        assert len(events) == 1, "three immediate hits should dedupe to one"
        s.close()

    def test_user_agent_recorded(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "o")
        client.get(
            f"/t/o/{token}",
            headers={"user-agent": "Mozilla/5.0 (TestBrowser)"},
        )
        s = session_factory()
        ev = s.execute(select(OpenEvent)).scalar_one()
        assert ev.user_agent == "Mozilla/5.0 (TestBrowser)"
        s.close()

    def test_apple_mpp_user_agent_flagged(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "o")
        client.get(
            f"/t/o/{token}",
            headers={"user-agent": "GoogleImageProxy"},
        )
        s = session_factory()
        ev = s.execute(select(OpenEvent)).scalar_one()
        assert ev.likely_apple_mpp is True
        s.close()

    def test_click_token_rejected_at_open_endpoint(
        self, client, session_factory, sent_message,
    ):
        """Token signed for 'c' shouldn't validate as 'o'."""
        token = tk.sign_token(sent_message.id, "c", url="https://x")
        res = client.get(f"/t/o/{token}")
        assert res.status_code == 204

        s = session_factory()
        assert s.execute(select(OpenEvent)).first() is None
        s.close()


# ── /t/c/{token} ────────────────────────────────────────────────────


class TestClickEndpoint:

    def test_happy_path_redirects_and_records(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "c", url="https://example.com/page")
        res = client.get(f"/t/c/{token}", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "https://example.com/page"

        s = session_factory()
        events = s.execute(select(ClickEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].original_url == "https://example.com/page"
        assert events[0].sent_message_id == sent_message.id
        s.close()

    def test_bad_token_redirects_to_safe_fallback(
        self, client, session_factory,
    ):
        res = client.get("/t/c/garbage", follow_redirects=False)
        assert res.status_code == 302
        # Default fallback — barclaysimpson.com unless overridden
        assert "barclaysimpson.com" in res.headers["location"]

        s = session_factory()
        assert s.execute(select(ClickEvent)).first() is None
        s.close()

    def test_repeat_clicks_NOT_deduped(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "c", url="https://example.com/x")
        client.get(f"/t/c/{token}", follow_redirects=False)
        client.get(f"/t/c/{token}", follow_redirects=False)
        client.get(f"/t/c/{token}", follow_redirects=False)

        s = session_factory()
        events = s.execute(select(ClickEvent)).scalars().all()
        assert len(events) == 3, "click events should not be deduped"
        s.close()

    def test_fast_click_flagged_as_scanner(
        self, session_factory, app,
    ):
        """A click within 120s of send → likely_scanner=true."""
        # Override the sent_at on a fresh row so it's "just sent"
        s = session_factory()
        co = CampaignOutput(dossier_id=f"d-{uuid4().hex[:6]}", model_used="gpt-test")
        s.add(co); s.flush()
        sm = SentMessage(
            campaign_output_id=co.id,
            sender_user_id="op", recipient_email="r@x",
            sequence_index=1, tone="formal",
            scheduled_for=datetime.utcnow(),
            sent_at=datetime.utcnow() - timedelta(seconds=10),  # just sent
            status="sent",
            graph_message_id="g", conversation_id="c",
            subject="s", body="b",
        )
        s.add(sm); s.commit()
        sm_id = sm.id
        s.close()

        with TestClient(app) as c:
            token = tk.sign_token(sm_id, "c", url="https://example.com")
            c.get(f"/t/c/{token}", follow_redirects=False)

        s = session_factory()
        ev = s.execute(select(ClickEvent)).scalar_one()
        assert ev.likely_scanner is True
        s.close()

    def test_scanner_user_agent_flagged(
        self, client, session_factory, sent_message,
    ):
        """sent_message has sent_at ~1h ago so the time-window heuristic
        won't fire — pure UA-based detection."""
        token = tk.sign_token(sent_message.id, "c", url="https://example.com")
        client.get(
            f"/t/c/{token}",
            headers={"user-agent": "Mimecast scanner"},
            follow_redirects=False,
        )
        s = session_factory()
        ev = s.execute(select(ClickEvent)).scalar_one()
        assert ev.likely_scanner is True
        s.close()

    def test_normal_user_click_not_flagged_as_scanner(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "c", url="https://example.com")
        client.get(
            f"/t/c/{token}",
            headers={"user-agent": "Mozilla/5.0 (Macintosh)"},
            follow_redirects=False,
        )
        s = session_factory()
        ev = s.execute(select(ClickEvent)).scalar_one()
        assert ev.likely_scanner is False
        s.close()

    def test_open_token_rejected_at_click_endpoint(
        self, client, session_factory, sent_message,
    ):
        token = tk.sign_token(sent_message.id, "o")
        res = client.get(f"/t/c/{token}", follow_redirects=False)
        assert res.status_code == 302
        assert "barclaysimpson.com" in res.headers["location"]
        s = session_factory()
        assert s.execute(select(ClickEvent)).first() is None
        s.close()
