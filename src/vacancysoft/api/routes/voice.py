"""Voice-layer endpoints — per-user tone prompts + voice samples.

  GET    /api/users/me/campaign-prompts   — all six tone prompts
  PUT    /api/users/me/campaign-prompts   — upsert any subset
  GET    /api/users/me/voice-samples      — operator's own audit view

Identity resolution uses ``get_current_user()`` from ``api/auth.py``.
A missing user row 401s just like the other ``/api/users/me/*``
endpoints — the frontend falls back to "no voice layer" and the
campaign prompt renders the pre-voice-layer default.

PUT semantics (matches the preferences endpoint shape):
  * missing key       → leave that tone alone
  * present key       → upsert that tone
  * empty string      → clear that tone (row kept, text emptied)

See ``.claude/plans/linear-meandering-rossum.md`` for full design.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from vacancysoft.api.auth import get_current_user
from vacancysoft.api.schemas import UserCampaignPromptsOut
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import UserCampaignPrompt, VoiceTrainingSample
from vacancysoft.intelligence.voice import (
    CAMPAIGN_TONES,
    load_tone_prompts,
    load_voice_samples,
)


router = APIRouter(tags=["voice"])


@router.get("/api/users/me/campaign-prompts", response_model=UserCampaignPromptsOut)
def get_my_campaign_prompts(request: Request):
    """Return all six tone prompts for the caller.

    Missing DB rows render as empty strings — the resolver treats
    empty and missing as identical (default guidance applies).
    """
    with SessionLocal() as s:
        user = get_current_user(request, s)
        prompts = load_tone_prompts(s, user)
    return UserCampaignPromptsOut(**prompts)


@router.put("/api/users/me/campaign-prompts")
def put_my_campaign_prompts(request: Request, payload: dict):
    """Upsert any subset of the caller's six tone prompts.

    Body shape: ``{"formal": "...", "informal": "...", ...}``. Any
    subset of the six tone keys is allowed. Unknown tone keys 400
    (early defensive signal; catches a frontend regression before it
    quietly writes garbage rows). Empty string on a known key
    clears that tone back to "no override" semantics.

    Returns the full post-merge view (all six tones) so the client
    SWR cache can reconcile in one step without a second GET.
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")

    unknown = [k for k in payload.keys() if k not in CAMPAIGN_TONES]
    if unknown:
        raise HTTPException(
            400,
            f"unknown tone keys: {sorted(unknown)}; allowed: {list(CAMPAIGN_TONES)}",
        )

    with SessionLocal() as s:
        user = get_current_user(request, s)
        # Load existing rows once, index by tone, upsert.
        existing = {
            row.tone: row
            for row in s.execute(
                select(UserCampaignPrompt).where(UserCampaignPrompt.user_id == user.id)
            ).scalars()
        }
        for tone, text in payload.items():
            if not isinstance(text, str):
                raise HTTPException(400, f"tone {tone!r} value must be a string, got {type(text).__name__}")
            row = existing.get(tone)
            if row is None:
                row = UserCampaignPrompt(
                    id=str(uuid4()),
                    user_id=user.id,
                    tone=tone,
                    instructions_text=text,
                )
                s.add(row)
            else:
                row.instructions_text = text
        s.commit()

        # Reload so the response reflects the committed state.
        prompts = load_tone_prompts(s, user)

    return UserCampaignPromptsOut(**prompts)


@router.get("/api/users/me/voice-samples")
def get_my_voice_samples(request: Request):
    """Return the caller's last-5-sent voice samples per sequence.

    Read-only audit view. The resolver consumes the same shape
    internally — this endpoint just lets the operator see what's on
    file. Samples are unioned from real sent_messages (status='sent')
    AND operator-authored training samples (migration 0012), newest
    first, capped at the resolver's window size.
    """
    with SessionLocal() as s:
        user = get_current_user(request, s)
        samples = load_voice_samples(s, user)
    # Already plain dicts with subject/body/tone — no schema coercion.
    return samples


@router.post("/api/users/me/voice-training-samples")
def post_my_voice_training_sample(request: Request, payload: dict):
    """Save an operator-authored voice training sample.

    Called from the Campaign Builder's "Save as training sample"
    button — lets operators seed the voice pool with hand-edited
    variants before the Graph send flow exists, so the voice layer
    has something to imitate on the next campaign regeneration.

    Body shape::

        {
          "sequence_index": 1-5,
          "tone": "informal",
          "subject": "...",
          "body": "...",
          "source_enriched_job_id": "..." (optional)
        }

    Validation:
      * tone must be one of the six campaign tones
      * sequence_index 1..5
      * subject + body both non-empty

    Returns the created row's id + created_at so the frontend can
    show a confirmation pill without a follow-up GET.
    """
    from uuid import uuid4

    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")

    tone = payload.get("tone")
    if tone not in CAMPAIGN_TONES:
        raise HTTPException(
            400,
            f"tone must be one of {list(CAMPAIGN_TONES)}; got {tone!r}",
        )

    try:
        sequence_index = int(payload.get("sequence_index"))
    except (TypeError, ValueError):
        raise HTTPException(400, "sequence_index must be an int 1-5")
    if sequence_index < 1 or sequence_index > 5:
        raise HTTPException(400, "sequence_index must be 1-5")

    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject:
        raise HTTPException(400, "subject must be non-empty")
    if not body:
        raise HTTPException(400, "body must be non-empty")
    # SentMessage.subject is String(500); mirror that cap so the two
    # sources can live side-by-side without accidental truncation.
    if len(subject) > 500:
        raise HTTPException(400, "subject must be ≤ 500 characters")

    source_enriched_job_id = payload.get("source_enriched_job_id")
    if source_enriched_job_id is not None:
        source_enriched_job_id = str(source_enriched_job_id).strip() or None

    with SessionLocal() as s:
        user = get_current_user(request, s)
        row = VoiceTrainingSample(
            id=str(uuid4()),
            user_id=user.id,
            sequence_index=sequence_index,
            tone=tone,
            subject=subject,
            body=body,
            source_enriched_job_id=source_enriched_job_id,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
