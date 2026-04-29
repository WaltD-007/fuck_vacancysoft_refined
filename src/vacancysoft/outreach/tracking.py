"""Open + click tracking for outbound outreach emails.

Pure module — no DB writes, no I/O. Caller (api/routes/tracking.py)
does the persistence.

Provides:

  * :func:`sign_token` / :func:`verify_token` — HMAC-SHA256 over a
    JSON payload describing one tracking event. Used to make the
    /t/o/<token> and /t/c/<token> URLs unforgeable without baking the
    sent_message_id into the URL plain-text.
  * :func:`inject_pixel` — append a 1×1 transparent pixel to an HTML
    email body, just before ``</body>`` (or at the end if the body
    has no closing body tag).
  * :func:`rewrite_links` — rewrite every ``<a href="http(s)://...">``
    in an HTML email body to point at /t/c/<token>, where the original
    URL is encoded in the token. Skips ``mailto:``, ``tel:``,
    anchor-only (``#...``) links, and any link that already points at
    the tracking domain (avoids double-rewrite on retried sends).
  * :func:`hash_ip` — HMAC the requester's IP with a derived salt so
    we get deterministic-per-salt-era dedupe without storing the raw
    IP.
  * :func:`is_likely_apple_mpp_ua` / :func:`is_likely_scanner_ua` —
    heuristic flags written to the event row at log time.
  * :func:`is_likely_scanner_click` — combined heuristic for clicks
    (time-window OR user-agent).

All functions read configuration from environment variables on each
call so a runtime change to ``PROSPERO_TRACKING_SECRET`` or
``OUTREACH_TRACKING_ENABLED`` takes effect without restart.

Kill switch: set ``OUTREACH_TRACKING_ENABLED=false`` to disable the
injection path entirely (raw bodies, no pixel, no link rewrite). Event
endpoints still record anything they receive — a stale link from an
older send will keep working — but no new tracking gets baked into
new sends. Useful for "is the issue tracking?" debugging.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from datetime import timedelta

# Public API — anything not in this list is internal.
__all__ = [
    "TRACKING_PIXEL_BYTES",
    "is_tracking_enabled",
    "sign_token",
    "verify_token",
    "inject_pixel",
    "rewrite_links",
    "hash_ip",
    "is_likely_apple_mpp_ua",
    "is_likely_scanner_ua",
    "is_likely_scanner_click",
]


# 1×1 transparent GIF — smallest valid GIF89a. Returned by /t/o.
TRACKING_PIXEL_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


_DEFAULT_INSECURE_SECRET = "dev-only-insecure-default"


def _secret() -> bytes:
    """Return the HMAC secret as bytes. Reads on every call so a
    runtime env-var change takes effect without restart. Falls back
    to a fixed dev-only string when unset — caller code logs a warning
    when this happens in non-dry-run mode (see api/routes/tracking.py).
    """
    return os.environ.get("PROSPERO_TRACKING_SECRET", _DEFAULT_INSECURE_SECRET).encode()


def is_tracking_enabled() -> bool:
    """Return False if ``OUTREACH_TRACKING_ENABLED`` is set to a falsy
    value (``false``, ``0``, ``no``). Default: enabled. The kill switch
    is checked by the caller (worker/outreach_tasks.py) before
    inject_pixel + rewrite_links — bypassed entirely when off."""
    raw = os.environ.get("OUTREACH_TRACKING_ENABLED", "true").strip().lower()
    return raw not in ("false", "0", "no")


# ── Token signing ────────────────────────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    # Restore padding stripped during encode.
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_token(sent_message_id: str, event_type: str, url: str | None = None) -> str:
    """Generate a tamper-resistant token for a tracking URL.

    Format: ``<base64url(payload_json)>.<base64url(sig[:16])>``
    where ``payload`` is ``{"m": sent_message_id, "t": event_type[, "u": url]}``
    and ``sig`` is the first 16 bytes of HMAC-SHA256 over the payload bytes.

    ``event_type`` is ``"o"`` (open) or ``"c"`` (click). For click tokens
    the original URL is included in the payload so the redirect endpoint
    knows where to send the recipient.

    16 bytes of HMAC truncation is plenty — we're protecting against
    forgery, not signing currency. Tokens have no expiry: opens and
    clicks can legitimately happen months after the send.
    """
    payload: dict[str, str] = {"m": sent_message_id, "t": event_type}
    if url is not None:
        payload["u"] = url
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()[:16]
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_token(token: str, *, expected_type: str | None = None) -> dict | None:
    """Verify and decode a token. Returns the payload dict on success,
    ``None`` on any failure (bad format, bad signature, JSON error, or
    type mismatch when ``expected_type`` is given).

    Returning ``None`` for every failure mode (rather than raising) is
    deliberate — the caller is HTTP-facing and shouldn't expose which
    failure mode happened. Pixel endpoint returns 204 on None; click
    endpoint redirects to a safe fallback. Either way the recipient
    sees nothing useful.
    """
    if not token or not isinstance(token, str):
        return None
    if "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None

    expected_sig = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if expected_type is not None and payload.get("t") != expected_type:
        return None
    return payload


# ── HTML manipulation ────────────────────────────────────────────────


_BODY_END_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def inject_pixel(html: str, sent_message_id: str, base_url: str) -> str:
    """Insert a 1×1 tracking pixel just before ``</body>``.

    If the body has no ``</body>`` tag (LLM output sometimes omits it),
    appends the pixel at the end of the string. ``base_url`` should be
    the public base URL for the tracking endpoints (e.g.
    ``http://localhost:8000`` in dev, ``https://link.barclaysimpson.com``
    in prod) — no trailing slash.
    """
    if not html:
        html = ""
    token = sign_token(sent_message_id, "o")
    pixel = (
        f'<img src="{base_url}/t/o/{token}" width="1" height="1" '
        f'style="display:none" alt="">'
    )
    match = _BODY_END_RE.search(html)
    if match:
        return html[: match.start()] + pixel + html[match.start():]
    return html + pixel


# Match <a ... href="..." ...> with single or double quotes around the URL.
# Captures: (attrs-before-href, quote-char, url, attrs-after-href).
# Doesn't try to be exhaustive on edge cases (HTML in attribute values
# etc.) — LLM-generated bodies are well-formed enough that simple regex
# is robust. Swap for BeautifulSoup if we see real-world breakage.
_LINK_RE = re.compile(
    r'(<a\b[^>]*?\s)href=(["\'])(https?://[^"\']+)\2([^>]*?>)',
    re.IGNORECASE,
)


def rewrite_links(html: str, sent_message_id: str, base_url: str) -> str:
    """Rewrite every absolute http(s) <a href> to point at /t/c/<token>.

    Skips mailto:, tel:, javascript:, and anchor-only links by virtue
    of the ``https?://`` prefix in the regex. Also skips links already
    pointing at ``base_url`` so retried sends or already-rewritten
    bodies don't double-rewrite.

    The original URL is encoded in the click token so the /t/c
    endpoint can 302 the recipient to the right place.
    """
    if not html or not base_url:
        return html

    base_normalised = base_url.rstrip("/")

    def _replace(match: re.Match) -> str:
        prefix, quote, url, suffix = match.groups()
        # Skip our own tracking domain to avoid wrapping a wrap.
        if url.startswith(base_normalised + "/t/"):
            return match.group(0)
        token = sign_token(sent_message_id, "c", url=url)
        new_url = f"{base_normalised}/t/c/{token}"
        return f"{prefix}href={quote}{new_url}{quote}{suffix}"

    return _LINK_RE.sub(_replace, html)


# ── Privacy: hash IP for storage ─────────────────────────────────────


def hash_ip(ip: str | None) -> str:
    """Return a 32-hex-char HMAC of the IP with a salt derived from
    the tracking secret. Empty string for missing/empty inputs.

    Hashing not encrypting: we never need the raw IP back, only
    "is this the same IP we saw before". The salt rotates implicitly
    when the underlying tracking secret rotates — old hashes stop
    correlating to new ones, which is fine; we don't need long-term
    cross-era dedupe.
    """
    if not ip:
        return ""
    salt = hashlib.sha256(b"prospero-ip-salt-v1:" + _secret()).digest()
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()[:32]


# ── User-agent heuristics ────────────────────────────────────────────


# Substring (case-insensitive) match — pragmatic over regex. Add to this
# list when we see new patterns in the wild.
_SCANNER_UA_PATTERNS = (
    "proofpoint",
    "mimecast",
    "msoffice365",
    "safelinks",
    "barracuda",
    "googleimageproxy",
    "forcepoint",
    "trustwave",
    "symantec",
    "cisco-ironport",
    "ironport",
    "fireeye",
)


def is_likely_scanner_ua(user_agent: str | None) -> bool:
    """True if the user-agent matches a known mail-security scanner."""
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    return any(p in ua_lower for p in _SCANNER_UA_PATTERNS)


def is_likely_scanner_click(
    user_agent: str | None,
    time_since_send: timedelta | None,
    *,
    pre_click_window_seconds: int = 120,
) -> bool:
    """Combined heuristic for click events.

    Flagged as scanner if EITHER:
      1. The click happened within ``pre_click_window_seconds`` of the
         email being sent (humans don't open + click that fast).
      2. The user-agent matches a known scanner pattern.

    Both signals are noisy alone but reliable in combination. We log
    every event regardless; this just sets a flag so aggregate counts
    can default to excluding them.
    """
    if (
        time_since_send is not None
        and time_since_send < timedelta(seconds=pre_click_window_seconds)
    ):
        return True
    return is_likely_scanner_ua(user_agent)


def is_likely_apple_mpp_ua(user_agent: str | None) -> bool:
    """True if the user-agent matches a known image-prefetch pattern.

    Conservative — Apple Mail Privacy Protection itself uses generic
    UA strings that are hard to detect by header alone. The reliable
    signal is Gmail's image proxy, which sets ``GoogleImageProxy``.
    Add detection patterns as we observe real traffic.
    """
    if not user_agent:
        return False
    return "GoogleImageProxy" in user_agent
