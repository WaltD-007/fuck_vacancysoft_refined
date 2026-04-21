"""Users endpoints — identity, profile, per-user preferences.

  GET    /api/users/me                      — current user's profile + prefs
  PATCH  /api/users/me/preferences          — shallow-merge a preferences patch
  GET    /api/users                         — admin list
  POST   /api/users                         — admin create

Identity resolution is in ``api/auth.py``; see its module docstring for
the "who is the caller" rules. Nothing in this file knows about Entra;
swapping the resolver to Entra headers is a one-liner in ``auth.py``.

Preferences merge semantics are **shallow at the top level**: the
incoming patch's top-level keys replace whole sub-dicts; top-level
keys not present in the patch are preserved. Deliberate — the frontend
always sends the full sub-dict (e.g. the whole ``dashboard_feed``
object), so recursive merge would only add ambiguity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from vacancysoft.api.auth import get_current_user, require_admin
from vacancysoft.api.schemas import UserCreate, UserOut
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import User


router = APIRouter(tags=["users"])


# ── Current-user endpoints ──────────────────────────────────────────


@router.get("/api/users/me", response_model=UserOut)
def get_me(request: Request):
    with SessionLocal() as s:
        user = get_current_user(request, s)
        # Build the response inside the session so relationships resolve
        # before detachment. (No relationships today, but safe pattern.)
        return UserOut.model_validate(user)


@router.patch("/api/users/me/preferences")
def patch_my_preferences(request: Request, patch: dict):
    """Shallow top-level merge into the caller's ``users.preferences``.

    Accepts any JSON object. Non-dict bodies 400. Returns the merged
    preferences dict.
    """
    if not isinstance(patch, dict):
        raise HTTPException(400, "body must be a JSON object")

    with SessionLocal() as s:
        user = get_current_user(request, s)
        existing = user.preferences or {}
        merged = {**existing, **patch}
        user.preferences = merged
        # SQLAlchemy's JSON column doesn't auto-detect in-place dict
        # mutation; without this flag, the UPDATE never fires.
        flag_modified(user, "preferences")
        s.commit()
        return merged


# ── Admin endpoints ─────────────────────────────────────────────────


@router.get("/api/users", response_model=list[UserOut])
def list_users(request: Request, _: None = Depends(require_admin)):
    with SessionLocal() as s:
        rows = s.execute(select(User).order_by(User.created_at)).scalars().all()
        return [UserOut.model_validate(u) for u in rows]


@router.post("/api/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    _: None = Depends(require_admin),
):
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(400, "display_name required")

    with SessionLocal() as s:
        existing = s.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"user already exists: {email}")

        user = User(
            email=email,
            display_name=display_name,
            entra_object_id=(body.entra_object_id.strip() if body.entra_object_id else None),
            role=body.role.strip() or "operator",
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        return UserOut.model_validate(user)
