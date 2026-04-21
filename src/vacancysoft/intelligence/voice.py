"""Voice layer — per-user tone prompts + last-5-sent voice samples.

Central helpers for building the ``user_context`` dict that
``resolve_campaign_prompt()`` consumes. Kept in its own module so the
FastAPI route, the worker (if ever wired for per-user pre-gen), the
CLI and the tests all pull from one place.

Cold-start behaviour is deliberate: when an operator has no authored
tone prompts AND no sent messages, ``build_user_context()`` still
returns a dict (so callers don't have to check for None) but the
resolver's ``_render_voice_layer()`` returns the empty string on that
shape — the campaign prompt then renders byte-identical to
pre-voice-layer output.

See ``.claude/plans/linear-meandering-rossum.md`` for the full design.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import SentMessage, User, UserCampaignPrompt


# Must match the six tones the campaign template enumerates. Adding
# a new tone later requires a template edit + a migration to seed
# the tone into existing rows, so the enumeration lives in one place.
CAMPAIGN_TONES: tuple[str, ...] = (
    "formal", "informal", "consultative", "direct", "candidate_spec", "technical",
)

# Rolling window size for voice samples, per sequence. Per operator
# spec: five recent sends per step. Increasing this bumps per-call
# input tokens (~150 tokens per sample × 5 sequences × window size) —
# keep it conservative.
VOICE_SAMPLE_WINDOW: int = 5


def load_tone_prompts(session: Session, user: User) -> dict[str, str]:
    """Return the six authored tone prompts for a user.

    Missing rows default to empty string — the resolver treats empty
    and missing as identical (both fall back to the base template's
    default guidance for that tone).
    """
    rows = session.execute(
        select(UserCampaignPrompt).where(UserCampaignPrompt.user_id == user.id)
    ).scalars().all()
    by_tone: dict[str, str] = {t: "" for t in CAMPAIGN_TONES}
    for row in rows:
        if row.tone in by_tone:
            by_tone[row.tone] = row.instructions_text or ""
    return by_tone


def load_voice_samples(session: Session, user: User) -> dict[int, list[dict[str, str]]]:
    """Return the last N sent messages per sequence for this user.

    Bridges ``users.id`` to ``SentMessage.sender_user_id`` via the
    user's email — ``sender_user_id`` is a String(255) that stores the
    Azure UPN / email, not a FK to users.id. This avoids a breaking
    migration on the already-populated sent_messages table.

    Only ``status='sent'`` rows are included — pending, failed and
    bounced sends are not voice signal. Returns an empty list per
    sequence when the user has nothing on record (cold start).
    """
    out: dict[int, list[dict[str, str]]] = {seq: [] for seq in range(1, 6)}
    if not user or not user.email:
        return out
    for seq in range(1, 6):
        rows = session.execute(
            select(SentMessage)
            .where(SentMessage.sender_user_id == user.email)
            .where(SentMessage.sequence_index == seq)
            .where(SentMessage.status == "sent")
            .order_by(SentMessage.sent_at.desc())
            .limit(VOICE_SAMPLE_WINDOW)
        ).scalars().all()
        out[seq] = [
            {
                "subject": row.subject or "",
                "body": row.body or "",
                "tone": row.tone or "",
            }
            for row in rows
        ]
    return out


def build_user_context(session: Session, user: User | None) -> dict[str, Any] | None:
    """Assemble the ``user_context`` dict the resolver consumes.

    Returns None when ``user`` is None (no operator identity
    resolved) so callers can keep passing it straight through to
    ``resolve_campaign_prompt(user_context=...)`` without branching.

    When ``user`` is populated but has no authored prompts and no
    sent messages, a populated-but-empty-shaped dict is returned
    rather than None. The resolver's ``_render_voice_layer()`` checks
    for actual content and returns an empty string on that shape,
    so the campaign prompt still renders byte-identical to pre-voice-
    layer output — cold-start path is indistinguishable from no user.
    """
    if user is None:
        return None
    return {
        "user_id": user.id,
        "display_name": user.display_name or "",
        "email": user.email or "",
        "tone_prompts": load_tone_prompts(session, user),
        "voice_samples_by_step": load_voice_samples(session, user),
    }
