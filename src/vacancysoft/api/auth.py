"""Identity resolution for the Prospero API.

Tiny module — one function: :func:`get_current_user`. Called at the
top of any route that needs the caller's identity (currently just the
``/api/users/me*`` routes; future routes that need ownership checks
will use it too).

Resolution order:

1. If ``X-Prospero-User-Email`` request header is set, look up by
   email. 404 if the email doesn't map to an active user.
2. Else, if exactly ONE active user exists in the DB, return that
   user (single-user-mode fallback — the dev / small-team default).
3. Else (zero active users, or ≥2 active users with no header), 401.

When Entra auth lands post-Keybridge-approval: swap the header name
from ``X-Prospero-User-Email`` to ``X-MS-CLIENT-PRINCIPAL-NAME``
(Azure Easy Auth's default) and look up by the decoded UPN. No other
code change needed anywhere else in the app.

Also handles ``last_seen_at`` observability — debounced to one DB
write per user per minute so routing overhead stays flat.

Optional admin guard: if ``PROSPERO_ADMIN_TOKEN`` env var is set, the
list / create endpoints require a matching ``X-Prospero-Admin-Token``
header. If the env var is unset (dev default), admin endpoints are
open. Production ``.env`` should set it; dev doesn't need to.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import User


_LAST_SEEN_DEBOUNCE = timedelta(seconds=60)


def get_current_user(request: Request, session: Session) -> User:
    """Return the User row for the caller of this request.

    Raises :class:`HTTPException`:
      - 404 if a supplied email header doesn't match any active user
      - 401 if identity can't be resolved (no header + no single user
        / no users at all / multiple users with no header)
    """
    email = request.headers.get("X-Prospero-User-Email", "").strip().lower()
    if email:
        user = session.execute(
            select(User).where(User.email == email, User.active.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(404, f"unknown user: {email}")
    else:
        # Single-user-mode fallback. For a small team or a dev machine
        # where there's exactly one operator, this means the app "just
        # works" after `prospero user add` with no header plumbing.
        active = session.execute(
            select(User).where(User.active.is_(True))
        ).scalars().all()
        if len(active) == 1:
            user = active[0]
        elif not active:
            raise HTTPException(
                401,
                "no active users; bootstrap with `prospero user add --email X --display-name Y`",
            )
        else:
            raise HTTPException(
                401,
                "ambiguous identity: multiple active users, send X-Prospero-User-Email header",
            )

    # last_seen_at observability — debounced so a busy route doesn't
    # hammer the DB with one write per request.
    now = datetime.utcnow()
    if user.last_seen_at is None or now - user.last_seen_at > _LAST_SEEN_DEBOUNCE:
        user.last_seen_at = now
        session.commit()
    return user


def require_admin(request: Request) -> None:
    """Guard for admin-only endpoints (GET /api/users, POST /api/users).

    If ``PROSPERO_ADMIN_TOKEN`` env var is set, request must carry
    a matching ``X-Prospero-Admin-Token`` header. Otherwise no-op
    (dev default).

    Raises :class:`HTTPException` 403 on mismatch, 401 on missing
    header when the env var is set.
    """
    expected = os.environ.get("PROSPERO_ADMIN_TOKEN", "").strip()
    if not expected:
        return  # dev — admin endpoints open
    provided = request.headers.get("X-Prospero-Admin-Token", "").strip()
    if not provided:
        raise HTTPException(401, "admin endpoint requires X-Prospero-Admin-Token header")
    if provided != expected:
        raise HTTPException(403, "invalid admin token")
