"""Open + click tracking HTTP endpoints.

Two unauthenticated endpoints — they're hit by recipients' mail
clients, not by logged-in users:

  GET /t/o/{token}   ->  200 image/gif (1×1 transparent pixel)
                          + open_events row inserted (if dedupe allows)
  GET /t/c/{token}   ->  302 to original URL
                          + click_events row inserted (always)

Bad tokens NEVER reveal that they're bad — pixel returns 204, click
redirects to a safe fallback. Recipients see nothing useful.

These endpoints MUST be carved out of any auth middleware (Easy Auth's
"allowed anonymous" list, future API gateway rules, etc.) — mail
clients don't have sessions.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import ClickEvent, OpenEvent, SentMessage
from vacancysoft.outreach.tracking import (
    TRACKING_PIXEL_BYTES,
    hash_ip,
    is_likely_apple_mpp_ua,
    is_likely_scanner_click,
    verify_token,
)

logger = logging.getLogger(__name__)


router = APIRouter(tags=["tracking"])


_OPEN_DEDUPE_WINDOW_SECONDS = 60


# Used when a click token is bad — we still issue a 302 (so a scanner
# can't tell whether the link was forged or just expired) but to a
# benign target. Override with TRACKING_FALLBACK_URL if BS prefer a
# different one (e.g. their main site).
_DEFAULT_FALLBACK_URL = "https://www.barclaysimpson.com"


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For when present (we'll
    sit behind Front Door in prod). Caller should already know not to
    use this for security decisions — it's just for dedupe-by-hash."""
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        # Front Door / App Gateway prepends the original client IP; take
        # the first comma-separated value.
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


@router.get("/t/o/{token}")
async def track_open(token: str, request: Request) -> Response:
    """Log an open event and return a 1×1 transparent gif.

    On bad token: return 204 (no content) — same shape as a successful
    pixel hit from the recipient's perspective (mail clients don't
    care about the response body for an <img>), no info leaked about
    why the token failed.
    """
    payload = verify_token(token, expected_type="o")
    if payload is None or not payload.get("m"):
        return Response(status_code=204)

    sent_message_id: str = payload["m"]
    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip(request)

    try:
        with SessionLocal() as s:
            # Verify the sent_message exists — defends against a token
            # whose sent_message_id no longer points at a real row
            # (deleted campaigns, bad data).
            sent = s.execute(
                select(SentMessage).where(SentMessage.id == sent_message_id)
            ).scalar_one_or_none()
            if sent is None:
                logger.info(
                    "tracking.open token references missing sent_message id=%s",
                    sent_message_id,
                )
                return Response(
                    content=TRACKING_PIXEL_BYTES,
                    media_type="image/gif",
                )

            # Dedupe within 60s — Outlook preview pane fires twice.
            cutoff = datetime.utcnow() - timedelta(
                seconds=_OPEN_DEDUPE_WINDOW_SECONDS
            )
            recent = s.execute(
                select(OpenEvent)
                .where(OpenEvent.sent_message_id == sent_message_id)
                .where(OpenEvent.opened_at >= cutoff)
                .order_by(OpenEvent.opened_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if recent is not None:
                logger.debug(
                    "tracking.open deduped within window sent_message=%s",
                    sent_message_id,
                )
            else:
                row = OpenEvent(
                    id=str(uuid4()),
                    sent_message_id=sent_message_id,
                    opened_at=datetime.utcnow(),
                    user_agent=user_agent,
                    ip_hash=hash_ip(client_ip),
                    likely_apple_mpp=is_likely_apple_mpp_ua(user_agent),
                )
                s.add(row)
                s.commit()
                logger.info(
                    "tracking.open recorded sent_message=%s mpp=%s",
                    sent_message_id, row.likely_apple_mpp,
                )
    except Exception as exc:  # pragma: no cover — defensive
        # Never let a tracking write fail the pixel response — recipient
        # waits for a network round-trip otherwise. Log and continue.
        logger.exception("tracking.open write failed: %s", exc)

    return Response(content=TRACKING_PIXEL_BYTES, media_type="image/gif")


@router.get("/t/c/{token}")
async def track_click(token: str, request: Request) -> Response:
    """Log a click event and 302 to the original URL.

    On bad token: 302 to a safe fallback URL, no log row written.
    """
    fallback = os.environ.get("TRACKING_FALLBACK_URL", _DEFAULT_FALLBACK_URL)
    payload = verify_token(token, expected_type="c")
    if payload is None or not payload.get("m") or not payload.get("u"):
        return RedirectResponse(fallback, status_code=302)

    sent_message_id: str = payload["m"]
    original_url: str = payload["u"]
    user_agent = request.headers.get("user-agent")
    client_ip = _client_ip(request)

    try:
        with SessionLocal() as s:
            sent = s.execute(
                select(SentMessage).where(SentMessage.id == sent_message_id)
            ).scalar_one_or_none()
            if sent is None:
                logger.info(
                    "tracking.click token references missing sent_message id=%s",
                    sent_message_id,
                )
                return RedirectResponse(original_url, status_code=302)

            time_since_send: timedelta | None = None
            if sent.sent_at is not None:
                time_since_send = datetime.utcnow() - sent.sent_at

            row = ClickEvent(
                id=str(uuid4()),
                sent_message_id=sent_message_id,
                original_url=original_url,
                clicked_at=datetime.utcnow(),
                user_agent=user_agent,
                ip_hash=hash_ip(client_ip),
                likely_scanner=is_likely_scanner_click(user_agent, time_since_send),
            )
            s.add(row)
            s.commit()
            logger.info(
                "tracking.click recorded sent_message=%s scanner=%s",
                sent_message_id, row.likely_scanner,
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("tracking.click write failed: %s", exc)

    return RedirectResponse(original_url, status_code=302)
