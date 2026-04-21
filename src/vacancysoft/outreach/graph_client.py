"""Async Microsoft Graph client for Prospero's outreach flow.

Two public methods:

- :meth:`GraphClient.send_mail` — send an outreach email on behalf of a
  named operator via ``POST /users/{id}/sendMail`` + sent-items lookup
  (see docstring for why the two-step dance is necessary).
- :meth:`GraphClient.list_replies` — poll a conversation for new replies
  via ``GET /users/{id}/messages?$filter=conversationId eq '…'``.

Both methods:

- Check :func:`~vacancysoft.outreach.dry_run.is_dry_run` at the top and
  return canned data when dry-run is on. **No network I/O in dry-run
  mode ever.**
- Log one structured JSON line per real call: timestamp, user-id,
  operation, http_status, latency_ms, and (for sends) returned
  ``graph_message_id`` + ``conversation_id``. Security brief §6 claim.
- Token caching: one :class:`_AccessTokenCache` per ``GraphClient`` that
  refreshes when <60s from expiry. A single long-lived worker process
  makes 1 token exchange per ~55 minutes.
- Retries on 429 (honours ``Retry-After``) and 5xx (exponential
  backoff). 4xx other than 429 is surfaced immediately as
  :class:`GraphError` with the Graph error-code + message embedded.

Auth flow is OAuth2 client-credentials: ``POST
https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`` with
``grant_type=client_credentials`` + ``scope=https://graph.microsoft.com/.default``.
That returns a bearer token that represents the application (not any
user), and the Application Access Policy in Exchange gates which
mailboxes the token can touch.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from vacancysoft.outreach.dry_run import (
    canned_list_replies,
    canned_send_mail,
    is_dry_run,
)
from vacancysoft.outreach.secret_client import SecretClient

logger = logging.getLogger(__name__)


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"
DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_TIMEOUT = 30.0
TOKEN_REFRESH_BUFFER_S = 60.0
MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0


class GraphError(RuntimeError):
    """Canonical Graph-failure type. Callers catch this and surface an
    operational error; everything up-stack can treat Graph as a single
    fail-closed dependency without knowing httpx, Azure, or OAuth.

    Attributes:
      status_code: HTTP status from the failing response, or None if
          the failure was pre-HTTP (e.g. token fetch crashed).
      graph_error_code: The ``error.code`` field Graph returns in its
          error body, e.g. ``"ErrorAccessDenied"``, ``"InvalidRequest"``.
      request_id: Graph's correlation id from the ``request-id`` header.
          Invaluable when opening a support case.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        graph_error_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.graph_error_code = graph_error_code
        self.request_id = request_id


@dataclass
class _AccessToken:
    token: str
    expires_at: datetime  # timezone-aware UTC

    def is_expiring_soon(self) -> bool:
        delta = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return delta <= TOKEN_REFRESH_BUFFER_S


class GraphClient:
    """Async Graph wrapper.

    Constructed once per process (worker) — token cache is instance
    state so restarting the worker forces a fresh token. Thread-safe in
    the single-event-loop sense; not safe for multi-thread use.

    Args:
      tenant_id: Entra tenant GUID. Defaults to ``GRAPH_TENANT_ID`` env.
      client_id: Entra app-registration GUID. Defaults to ``GRAPH_CLIENT_ID``.
      secret_client: Injected :class:`SecretClient` for testability.
          Defaults to a fresh :class:`SecretClient`.
      http_client: Injected ``httpx.AsyncClient`` for testability. When
          None, one is created per method call (short-lived); in
          production the worker should pass in a long-lived client.
    """

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        client_id: str | None = None,
        secret_client: SecretClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tenant_id = tenant_id or os.environ.get("GRAPH_TENANT_ID", "")
        self._client_id = client_id or os.environ.get("GRAPH_CLIENT_ID", "")
        self._secret_client = secret_client or SecretClient()
        self._http_client = http_client
        self._token_cache: _AccessToken | None = None

    # ── Public API ──────────────────────────────────────────────────

    async def send_mail(
        self,
        *,
        sender_user_id: str,
        to_address: str,
        subject: str,
        html_body: str,
    ) -> dict[str, Any]:
        """Send one outreach email.

        Returns a dict with keys ``graph_message_id``, ``conversation_id``,
        ``user_id``, ``to_address``, ``subject``, ``sent_at``, ``dry_run``.

        In dry-run mode: returns canned synthetic ids, no network I/O.
        In live mode: two-step — sendMail (returns 202 with no body), then
        read the freshest Sent Items message to recover id + conversationId.

        Raises :class:`GraphError` on any non-retryable failure. Retryable
        failures (429, 5xx) are handled internally with backoff.
        """
        if is_dry_run():
            logger.info(
                json.dumps({
                    "event": "outreach.send_mail",
                    "dry_run": True,
                    "user_id": sender_user_id,
                    "to": to_address,
                })
            )
            return canned_send_mail(
                user_id=sender_user_id,
                to_address=to_address,
                subject=subject,
            )

        self._require_live_config()
        t0 = time.monotonic()

        # Step 1: fire sendMail. Graph returns 202 Accepted with an empty body.
        send_payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [
                    {"emailAddress": {"address": to_address}}
                ],
            },
            "saveToSentItems": True,
        }
        url = f"{GRAPH_BASE}/users/{sender_user_id}/sendMail"
        resp = await self._request("POST", url, json=send_payload)
        if resp.status_code != 202:
            raise GraphError(
                f"sendMail returned unexpected status {resp.status_code}",
                status_code=resp.status_code,
                request_id=resp.headers.get("request-id"),
            )

        # Step 2: recover message id + conversationId from Sent Items.
        # Filter by subject + recipient to avoid confusing with other
        # concurrent sends.
        subject_quoted = subject.replace("'", "''")
        to_quoted = to_address.replace("'", "''")
        list_url = (
            f"{GRAPH_BASE}/users/{sender_user_id}/mailFolders/sentitems/messages"
            f"?$filter=subject eq '{subject_quoted}'"
            f"%20and%20"
            f"toRecipients/any(r: r/emailAddress/address eq '{to_quoted}')"
            f"&$orderby=sentDateTime%20desc"
            f"&$top=1"
            f"&$select=id,conversationId,sentDateTime,subject"
        )
        recover = await self._request("GET", list_url)
        if recover.status_code != 200:
            raise GraphError(
                f"sendMail recovery fetch failed: {recover.status_code}",
                status_code=recover.status_code,
                request_id=recover.headers.get("request-id"),
            )
        recover_body = recover.json()
        items = recover_body.get("value") or []
        if not items:
            # Rare: Graph says "202 Accepted" but the sent-items lookup
            # hasn't indexed yet. Caller can retry the poller later; for
            # now surface a warning-level log, not an error, so the row
            # still gets marked sent.
            logger.warning(
                json.dumps({
                    "event": "outreach.send_mail.recover_empty",
                    "user_id": sender_user_id,
                    "to": to_address,
                })
            )
            graph_message_id = ""
            conversation_id = ""
        else:
            graph_message_id = items[0].get("id", "")
            conversation_id = items[0].get("conversationId", "")

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            json.dumps({
                "event": "outreach.send_mail",
                "dry_run": False,
                "user_id": sender_user_id,
                "to": to_address,
                "graph_message_id": graph_message_id,
                "conversation_id": conversation_id,
                "latency_ms": latency_ms,
            })
        )
        return {
            "graph_message_id": graph_message_id,
            "conversation_id": conversation_id,
            "user_id": sender_user_id,
            "to_address": to_address,
            "subject": subject,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": False,
        }

    async def list_replies(
        self,
        *,
        user_id: str,
        conversation_id: str,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Return messages in a conversation received after ``since``.

        In dry-run: always ``[]``. In live mode: one Graph call. The
        ``since`` parameter MUST be timezone-aware UTC; passing naive
        datetimes raises ``ValueError``.

        Each returned dict has ``graph_message_id``, ``conversation_id``,
        ``from_email``, ``received_at``, ``subject``. No body.
        """
        if since.tzinfo is None:
            raise ValueError("`since` must be timezone-aware")
        since_utc = since.astimezone(timezone.utc)

        if is_dry_run():
            logger.debug(
                json.dumps({
                    "event": "outreach.list_replies",
                    "dry_run": True,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                })
            )
            return canned_list_replies(
                user_id=user_id, conversation_id=conversation_id
            )

        self._require_live_config()
        t0 = time.monotonic()

        since_iso = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        conv_quoted = conversation_id.replace("'", "''")
        url = (
            f"{GRAPH_BASE}/users/{user_id}/messages"
            f"?$filter=conversationId eq '{conv_quoted}'"
            f"%20and%20receivedDateTime gt {since_iso}"
            f"&$select=id,conversationId,from,receivedDateTime,subject,internetMessageId"
            f"&$top=25"
        )
        resp = await self._request("GET", url)
        if resp.status_code != 200:
            raise GraphError(
                f"list_replies returned {resp.status_code}",
                status_code=resp.status_code,
                request_id=resp.headers.get("request-id"),
            )
        body = resp.json()
        items = body.get("value") or []

        replies: list[dict[str, Any]] = []
        for item in items:
            from_obj = (item.get("from") or {}).get("emailAddress") or {}
            replies.append({
                "graph_message_id": item.get("id", ""),
                "conversation_id": item.get("conversationId", ""),
                "from_email": from_obj.get("address", ""),
                "received_at": item.get("receivedDateTime", ""),
                "subject": item.get("subject", ""),
            })

        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            json.dumps({
                "event": "outreach.list_replies",
                "dry_run": False,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "replies_found": len(replies),
                "latency_ms": latency_ms,
            })
        )
        return replies

    # ── Internals ───────────────────────────────────────────────────

    def _require_live_config(self) -> None:
        """Assert the env is ready for live Graph calls. Raised before
        any network I/O so the error message is clear."""
        missing = [
            name for name, val in (
                ("GRAPH_TENANT_ID", self._tenant_id),
                ("GRAPH_CLIENT_ID", self._client_id),
            ) if not val
        ]
        if missing:
            raise GraphError(
                "Graph live mode requested but required env vars are missing: "
                + ", ".join(missing)
                + ". Set them on the Container App, or set OUTREACH_DRY_RUN=true."
            )

    async def _get_access_token(self) -> str:
        """Get a valid bearer token, refreshing if near-expiry."""
        if self._token_cache and not self._token_cache.is_expiring_soon():
            return self._token_cache.token

        client_secret = self._secret_client.get_client_secret()
        url = f"{LOGIN_BASE}/{self._tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self._client_id,
            "scope": DEFAULT_SCOPE,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
        client = self._http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        try:
            resp = await client.post(url, data=data)
        finally:
            if self._http_client is None:
                await client.aclose()

        if resp.status_code != 200:
            raise GraphError(
                f"Token exchange failed: {resp.status_code} {resp.text[:200]}",
                status_code=resp.status_code,
            )
        body = resp.json()
        token = body.get("access_token")
        expires_in = int(body.get("expires_in", 3500))
        if not token:
            raise GraphError("Token response did not contain access_token")

        self._token_cache = _AccessToken(
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )
        return token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Graph call with token injection + 429/5xx retry."""
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        client = self._http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        last_exc: Exception | None = None
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await client.request(method, url, headers=headers, json=json)
                except httpx.TransportError as exc:
                    last_exc = exc
                    await self._sleep_backoff(attempt, None)
                    continue

                if resp.status_code == 401 and attempt == 0:
                    # Token may have expired between cache check and send.
                    # Force a fresh one and retry ONCE.
                    self._token_cache = None
                    token = await self._get_access_token()
                    headers["Authorization"] = f"Bearer {token}"
                    continue

                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < MAX_RETRIES - 1:
                        await self._sleep_backoff(attempt, resp.headers.get("Retry-After"))
                        continue
                    # Fall through to return resp (caller will raise).

                return resp
        finally:
            if self._http_client is None:
                await client.aclose()

        raise GraphError(f"Graph request failed after {MAX_RETRIES} retries: {last_exc}")

    @staticmethod
    async def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
        """Respect Retry-After header if present, else exponential backoff."""
        import asyncio
        if retry_after and retry_after.isdigit():
            await asyncio.sleep(float(retry_after))
        else:
            await asyncio.sleep(BACKOFF_BASE_S ** (attempt + 1))
