"""End-to-end tests for the /api/users routes.

Uses FastAPI's TestClient against an in-memory SQLite DB. We patch
``SessionLocal`` inside the ``routes.users`` module so every route
handler opens a session against the test DB rather than the real
engine.

Covers:
  - identity resolver's three branches (header / single-user / ambiguous)
  - GET /api/users/me: happy path, 404 on unknown email, 401 on no users
  - PATCH /api/users/me/preferences: merge semantics (replace top-level,
    preserve other keys), non-dict body → 400
  - POST /api/users: happy path, duplicate email → 409
  - GET /api/users: admin list
  - PROSPERO_ADMIN_TOKEN guard: set → blocks without header; unset →
    admin endpoints open
  - last_seen_at: set on first request, unchanged on second within
    the 60s debounce window
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vacancysoft.api import auth as auth_module
from vacancysoft.api.routes import users as users_module
from vacancysoft.api.server import app
from vacancysoft.db.models import Base, User


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory():
    # In-memory SQLite is per-connection by default. Every time the
    # route opens a new Session (via SessionLocal()), it'd get a fresh
    # empty DB — so StaticPool + check_same_thread=False is required
    # to share one in-memory DB across all sessions in the test.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, session_factory):
    """Patch SessionLocal in both the routes module (for handlers) and
    the auth module (indirectly, since it's imported via the routes).
    Return a FastAPI TestClient."""
    monkeypatch.setattr(users_module, "SessionLocal", session_factory)
    # Clear any residual admin token from outer env so tests control it.
    monkeypatch.delenv("PROSPERO_ADMIN_TOKEN", raising=False)
    return TestClient(app)


def _add_user(session_factory, email: str, *, active: bool = True, display_name: str = "Test") -> str:
    with session_factory() as s:
        u = User(email=email.lower(), display_name=display_name, active=active)
        s.add(u)
        s.commit()
        return u.id


# ── GET /api/users/me ───────────────────────────────────────────────


class TestGetMe:

    def test_happy_path_with_header(self, client, session_factory) -> None:
        _add_user(session_factory, "one@firm.com", display_name="One")
        _add_user(session_factory, "two@firm.com", display_name="Two")
        resp = client.get(
            "/api/users/me",
            headers={"X-Prospero-User-Email": "two@firm.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "two@firm.com"
        assert body["display_name"] == "Two"
        assert body["role"] == "operator"
        assert body["active"] is True
        assert body["preferences"] == {}

    def test_single_user_fallback_no_header(self, client, session_factory) -> None:
        _add_user(session_factory, "only@firm.com")
        resp = client.get("/api/users/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "only@firm.com"

    def test_unknown_email_header_404(self, client, session_factory) -> None:
        _add_user(session_factory, "real@firm.com")
        resp = client.get(
            "/api/users/me",
            headers={"X-Prospero-User-Email": "nobody@firm.com"},
        )
        assert resp.status_code == 404

    def test_zero_users_no_header_401(self, client, session_factory) -> None:
        resp = client.get("/api/users/me")
        assert resp.status_code == 401
        assert "bootstrap" in resp.json()["detail"].lower()

    def test_multiple_users_no_header_401(self, client, session_factory) -> None:
        _add_user(session_factory, "one@firm.com")
        _add_user(session_factory, "two@firm.com")
        resp = client.get("/api/users/me")
        assert resp.status_code == 401
        assert "ambiguous" in resp.json()["detail"].lower()

    def test_inactive_user_treated_as_absent(self, client, session_factory) -> None:
        """Inactive user should 404 on email lookup, and shouldn't
        count toward the single-user-mode fallback."""
        _add_user(session_factory, "gone@firm.com", active=False)
        resp = client.get(
            "/api/users/me",
            headers={"X-Prospero-User-Email": "gone@firm.com"},
        )
        assert resp.status_code == 404
        # With zero ACTIVE users, no-header falls to the 401 branch.
        resp = client.get("/api/users/me")
        assert resp.status_code == 401


# ── PATCH /api/users/me/preferences ─────────────────────────────────


class TestPatchPreferences:

    def test_first_patch_sets_section(self, client, session_factory) -> None:
        _add_user(session_factory, "me@firm.com")
        resp = client.patch(
            "/api/users/me/preferences",
            json={
                "dashboard_feed": {
                    "category": "risk",
                    "country": "UK",
                    "sub_specialism": "Credit Risk",
                    "employment_type": "Permanent",
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["dashboard_feed"]["category"] == "risk"

    def test_second_patch_preserves_other_sections(self, client, session_factory) -> None:
        """PATCHing leads_page must not clobber dashboard_feed."""
        _add_user(session_factory, "me@firm.com")
        client.patch(
            "/api/users/me/preferences",
            json={"dashboard_feed": {"category": "risk"}},
        )
        resp = client.patch(
            "/api/users/me/preferences",
            json={"leads_page": {"sort_by": "score"}},
        )
        assert resp.status_code == 200
        merged = resp.json()
        assert merged["dashboard_feed"]["category"] == "risk"
        assert merged["leads_page"]["sort_by"] == "score"

    def test_replacing_same_section_overwrites_whole_subdict(
        self, client, session_factory
    ) -> None:
        """Shallow merge at top level: re-PATCHing dashboard_feed
        with only one key wipes the other three (by design)."""
        _add_user(session_factory, "me@firm.com")
        client.patch(
            "/api/users/me/preferences",
            json={"dashboard_feed": {"category": "risk", "country": "UK"}},
        )
        resp = client.patch(
            "/api/users/me/preferences",
            json={"dashboard_feed": {"category": "compliance"}},
        )
        merged = resp.json()
        assert merged["dashboard_feed"]["category"] == "compliance"
        assert "country" not in merged["dashboard_feed"]

    def test_persists_across_a_me_reload(self, client, session_factory) -> None:
        _add_user(session_factory, "me@firm.com")
        client.patch(
            "/api/users/me/preferences",
            json={"dashboard_feed": {"category": "risk"}},
        )
        resp = client.get("/api/users/me")
        assert resp.json()["preferences"]["dashboard_feed"]["category"] == "risk"

    def test_401_if_no_user(self, client, session_factory) -> None:
        resp = client.patch(
            "/api/users/me/preferences",
            json={"dashboard_feed": {}},
        )
        assert resp.status_code == 401


# ── POST /api/users (admin create) ──────────────────────────────────


class TestCreateUser:

    def test_happy_path(self, client) -> None:
        resp = client.post(
            "/api/users",
            json={"email": "new@firm.com", "display_name": "New"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "new@firm.com"
        assert body["role"] == "operator"
        assert body["active"] is True

    def test_email_lowercased(self, client) -> None:
        resp = client.post(
            "/api/users",
            json={"email": "UPPER@FIRM.COM", "display_name": "U"},
        )
        assert resp.json()["email"] == "upper@firm.com"

    def test_duplicate_email_409(self, client) -> None:
        client.post("/api/users", json={"email": "dup@firm.com", "display_name": "A"})
        resp = client.post(
            "/api/users",
            json={"email": "dup@firm.com", "display_name": "B"},
        )
        assert resp.status_code == 409


# ── Admin guard ─────────────────────────────────────────────────────


class TestAdminGuard:

    def test_open_by_default_when_env_unset(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PROSPERO_ADMIN_TOKEN", raising=False)
        resp = client.get("/api/users")
        assert resp.status_code == 200

    def test_blocks_without_header_when_env_set(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROSPERO_ADMIN_TOKEN", "secret-abc")
        resp = client.get("/api/users")
        assert resp.status_code == 401

    def test_blocks_on_wrong_token(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROSPERO_ADMIN_TOKEN", "secret-abc")
        resp = client.get(
            "/api/users",
            headers={"X-Prospero-Admin-Token": "WRONG"},
        )
        assert resp.status_code == 403

    def test_passes_with_correct_token(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROSPERO_ADMIN_TOKEN", "secret-abc")
        resp = client.get(
            "/api/users",
            headers={"X-Prospero-Admin-Token": "secret-abc"},
        )
        assert resp.status_code == 200


# ── last_seen_at debounce ───────────────────────────────────────────


class TestLastSeenDebounce:

    def test_set_on_first_request(self, client, session_factory) -> None:
        user_id = _add_user(session_factory, "me@firm.com")
        with session_factory() as s:
            assert s.get(User, user_id).last_seen_at is None
        client.get("/api/users/me")
        with session_factory() as s:
            assert s.get(User, user_id).last_seen_at is not None

    def test_not_updated_within_debounce_window(
        self, client, session_factory
    ) -> None:
        user_id = _add_user(session_factory, "me@firm.com")
        client.get("/api/users/me")
        with session_factory() as s:
            first = s.get(User, user_id).last_seen_at
        # Second request immediately — within 60s window — should NOT
        # update last_seen_at.
        client.get("/api/users/me")
        with session_factory() as s:
            second = s.get(User, user_id).last_seen_at
        assert first == second

    def test_updated_after_window_elapses(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force the debounce to 0s so we can assert the update path."""
        monkeypatch.setattr(auth_module, "_LAST_SEEN_DEBOUNCE", timedelta(seconds=0))
        user_id = _add_user(session_factory, "me@firm.com")
        client.get("/api/users/me")
        with session_factory() as s:
            first = s.get(User, user_id).last_seen_at
        time.sleep(0.02)  # SQLite datetime resolution
        client.get("/api/users/me")
        with session_factory() as s:
            second = s.get(User, user_id).last_seen_at
        assert second > first
