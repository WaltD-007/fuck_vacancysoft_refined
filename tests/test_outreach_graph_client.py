"""Tests for GraphClient.

Dry-run tests (majority): no httpx mocking needed — the client short-
circuits via ``is_dry_run()`` before touching the network.

Live-mode tests: use a fake ``httpx.AsyncClient`` injected via
constructor. We don't actually start a server; we pre-program the fake
to return specific responses and assert the client handled them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from vacancysoft.outreach.graph_client import (
    GraphClient,
    GraphError,
    LOGIN_BASE,
)
from vacancysoft.outreach.secret_client import SecretClient


# ── Fakes ───────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        json_body: dict[str, Any] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient``. Records every call; returns
    the next response from a queue; raises if queue is empty."""

    def __init__(self) -> None:
        self._queue: list[_FakeResponse | Exception] = []
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, resp: _FakeResponse | Exception) -> None:
        self._queue.append(resp)

    async def post(self, url: str, data: dict[str, Any] | None = None, **_: Any) -> _FakeResponse:
        self.calls.append({"method": "POST", "url": url, "data": data})
        return self._pop(url)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
        })
        return self._pop(url)

    def _pop(self, url: str) -> _FakeResponse:
        if not self._queue:
            raise AssertionError(f"FakeAsyncClient queue exhausted at url={url}")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        pass


class _StubSecretClient(SecretClient):
    def __init__(self, value: str = "live-secret") -> None:
        super().__init__()
        self._value = value

    def get_client_secret(self) -> str:
        return self._value


def _token_response(expires_in: int = 3600) -> _FakeResponse:
    return _FakeResponse(
        status_code=200,
        json_body={"access_token": "fake-token", "expires_in": expires_in},
    )


# ── Dry-run tests ───────────────────────────────────────────────────


class TestDryRunSend:
    """Dry-run send_mail returns canned synthetic ids and never hits network."""

    @pytest.mark.asyncio
    async def test_returns_synthetic_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)
        # Don't set any Graph env vars — must not matter in dry-run.
        monkeypatch.delenv("GRAPH_TENANT_ID", raising=False)
        monkeypatch.delenv("GRAPH_CLIENT_ID", raising=False)

        client = GraphClient()
        result = await client.send_mail(
            sender_user_id="op-1",
            to_address="hm@corp.com",
            subject="Hello",
            html_body="<p>hi</p>",
        )
        assert result["dry_run"] is True
        assert result["graph_message_id"].startswith("dryrun-msg-")
        assert result["conversation_id"].startswith("dryrun-conv-")

    @pytest.mark.asyncio
    async def test_dry_run_does_not_require_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with every env var absent, dry-run must succeed."""
        for key in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
                    "KEY_VAULT_URI", "OUTREACH_DRY_RUN"):
            monkeypatch.delenv(key, raising=False)

        out = await GraphClient().send_mail(
            sender_user_id="u", to_address="a@b", subject="s", html_body="b"
        )
        assert out["dry_run"] is True


class TestDryRunListReplies:
    @pytest.mark.asyncio
    async def test_always_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)

        result = await GraphClient().list_replies(
            user_id="op-1",
            conversation_id="conv-xyz",
            since=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_naive_since_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)

        with pytest.raises(ValueError, match="timezone-aware"):
            await GraphClient().list_replies(
                user_id="op",
                conversation_id="c",
                since=datetime(2026, 4, 21, 10),  # naive — no tzinfo
            )


# ── Live-mode tests ──────────────────────────────────────────────────


class TestLiveSendMail:
    """With OUTREACH_DRY_RUN=false the client issues token + sendMail +
    sent-items recovery calls, in that order."""

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "tenant-guid")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "client-guid")

        fake = _FakeAsyncClient()
        fake.enqueue(_token_response())  # token exchange
        fake.enqueue(_FakeResponse(status_code=202))  # sendMail — 202 empty
        fake.enqueue(_FakeResponse(  # sent-items recovery
            status_code=200,
            json_body={"value": [{"id": "AAMk=", "conversationId": "CONV-1",
                                  "sentDateTime": "2026-04-21T10:00:00Z",
                                  "subject": "Hello"}]},
        ))

        client = GraphClient(
            secret_client=_StubSecretClient(),
            http_client=fake,
        )
        result = await client.send_mail(
            sender_user_id="op-1",
            to_address="hm@corp.com",
            subject="Hello",
            html_body="<p>hi</p>",
        )
        assert result["graph_message_id"] == "AAMk="
        assert result["conversation_id"] == "CONV-1"
        assert result["dry_run"] is False

        # Call-order assertions
        assert len(fake.calls) == 3
        assert fake.calls[0]["url"].startswith(LOGIN_BASE)
        assert "/sendMail" in fake.calls[1]["url"]
        assert "/mailFolders/sentitems/messages" in fake.calls[2]["url"]

    @pytest.mark.asyncio
    async def test_missing_env_vars_raise_before_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.delenv("GRAPH_TENANT_ID", raising=False)
        monkeypatch.delenv("GRAPH_CLIENT_ID", raising=False)

        fake = _FakeAsyncClient()  # no responses queued — would AssertionError if called
        with pytest.raises(GraphError, match="GRAPH_TENANT_ID"):
            await GraphClient(
                secret_client=_StubSecretClient(),
                http_client=fake,
            ).send_mail(
                sender_user_id="u", to_address="a@b", subject="s", html_body="b"
            )
        assert fake.calls == []  # never made a network call

    @pytest.mark.asyncio
    async def test_sendmail_non_202_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        fake = _FakeAsyncClient()
        fake.enqueue(_token_response())
        fake.enqueue(_FakeResponse(
            status_code=400,
            json_body={"error": {"code": "ErrorInvalidRequest", "message": "bad"}},
            headers={"request-id": "req-1"},
        ))

        with pytest.raises(GraphError) as exc:
            await GraphClient(
                secret_client=_StubSecretClient(), http_client=fake
            ).send_mail(
                sender_user_id="u", to_address="a@b", subject="s", html_body="b"
            )
        assert exc.value.status_code == 400
        assert exc.value.request_id == "req-1"

    @pytest.mark.asyncio
    async def test_recovery_returns_empty_logs_warning_but_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Graph sometimes indexes Sent Items lazily. A 200+empty-list
        recovery response should not raise — just warn."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        fake = _FakeAsyncClient()
        fake.enqueue(_token_response())
        fake.enqueue(_FakeResponse(status_code=202))
        fake.enqueue(_FakeResponse(status_code=200, json_body={"value": []}))

        with caplog.at_level("WARNING"):
            result = await GraphClient(
                secret_client=_StubSecretClient(), http_client=fake
            ).send_mail(
                sender_user_id="u", to_address="a@b", subject="s", html_body="b"
            )
        assert result["graph_message_id"] == ""
        assert result["conversation_id"] == ""
        assert any("recover_empty" in r.message for r in caplog.records)


class TestLiveListReplies:

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        fake = _FakeAsyncClient()
        fake.enqueue(_token_response())
        fake.enqueue(_FakeResponse(
            status_code=200,
            json_body={"value": [{
                "id": "reply-1",
                "conversationId": "CONV-1",
                "from": {"emailAddress": {"address": "hm@corp.com", "name": "HM"}},
                "receivedDateTime": "2026-04-21T14:32:00Z",
                "subject": "Re: Hello",
            }]},
        ))

        out = await GraphClient(
            secret_client=_StubSecretClient(), http_client=fake
        ).list_replies(
            user_id="op-1",
            conversation_id="CONV-1",
            since=datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert len(out) == 1
        assert out[0]["graph_message_id"] == "reply-1"
        assert out[0]["from_email"] == "hm@corp.com"
        assert out[0]["subject"] == "Re: Hello"


class TestTokenCache:

    @pytest.mark.asyncio
    async def test_token_reused_within_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call should not re-hit the token endpoint."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("GRAPH_CLIENT_ID", "c")

        fake = _FakeAsyncClient()
        fake.enqueue(_token_response(expires_in=3600))  # Only ONE token call queued
        fake.enqueue(_FakeResponse(status_code=202))
        fake.enqueue(_FakeResponse(
            status_code=200,
            json_body={"value": [{"id": "A", "conversationId": "C",
                                  "sentDateTime": "2026-04-21T10:00:00Z",
                                  "subject": "s"}]},
        ))
        fake.enqueue(_FakeResponse(status_code=202))
        fake.enqueue(_FakeResponse(
            status_code=200,
            json_body={"value": [{"id": "B", "conversationId": "D",
                                  "sentDateTime": "2026-04-21T10:00:01Z",
                                  "subject": "s"}]},
        ))

        client = GraphClient(secret_client=_StubSecretClient(), http_client=fake)
        await client.send_mail(sender_user_id="u", to_address="a@b",
                               subject="s", html_body="b")
        await client.send_mail(sender_user_id="u", to_address="c@d",
                               subject="s", html_body="b")

        token_calls = [c for c in fake.calls if c["url"].startswith(LOGIN_BASE)]
        assert len(token_calls) == 1
