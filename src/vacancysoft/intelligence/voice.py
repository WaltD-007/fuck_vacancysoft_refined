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

from vacancysoft.db.models import (
    SentMessage,
    User,
    UserCampaignPrompt,
    VoiceTrainingSample,
)


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


def load_voice_samples(
    session: Session, user: User
) -> dict[str, dict[int, list[dict[str, str]]]]:
    """Return the last N voice samples per (tone, sequence) for this user.

    Shape: ``{tone: {sequence_index: [sample, ...]}}``. Each list is
    capped at ``VOICE_SAMPLE_WINDOW`` (5) entries, newest-first.
    Strict per-tone matching — a sample saved for the informal tone
    never enters the formal tone's pool. This is important because
    the six tones deliberately sound different: the point of training
    is to teach each tone's voice independently.

    Samples come from **two unioned sources**, newest-first:

      1. ``SentMessage`` rows with ``status='sent'`` — the authoritative
         voice signal once the Graph send flow exists and is actually
         delivering real emails. Bridged from ``users.id`` to
         ``SentMessage.sender_user_id`` via the user's email
         (``sender_user_id`` is a String(255) storing the Azure UPN).
      2. ``VoiceTrainingSample`` rows — operator-authored samples
         saved from the Campaign Builder's "Save as training sample"
         button (migration 0012, 2026-04-21). Bootstrap data so the
         voice layer has something to imitate before real sends start
         writing SentMessage rows.

    The two sources are merged, sorted by their respective timestamps
    (sent_at / created_at), and the top ``VOICE_SAMPLE_WINDOW`` per
    (tone, sequence) are returned. This gives training rows natural
    decay — once real sends are accumulating at operational volume,
    they push training rows out of the window automatically without
    anyone having to delete anything.

    Returns empty lists everywhere when the user has nothing on
    record (cold start). Keys for every tone are always present so
    callers don't have to check for None.
    """
    out: dict[str, dict[int, list[dict[str, str]]]] = {
        tone: {seq: [] for seq in range(1, 6)} for tone in CAMPAIGN_TONES
    }
    if not user:
        return out

    for tone in CAMPAIGN_TONES:
        for seq in range(1, 6):
            # Collect candidates from both sources. Each candidate is a
            # (timestamp, sample_dict) tuple so we can merge-sort cleanly.
            candidates: list[tuple[Any, dict[str, str]]] = []

            # Source 1 — real sent messages (status='sent' only).
            if user.email:
                sent_rows = session.execute(
                    select(SentMessage)
                    .where(SentMessage.sender_user_id == user.email)
                    .where(SentMessage.sequence_index == seq)
                    .where(SentMessage.tone == tone)
                    .where(SentMessage.status == "sent")
                    .order_by(SentMessage.sent_at.desc())
                    .limit(VOICE_SAMPLE_WINDOW)
                ).scalars().all()
                for row in sent_rows:
                    candidates.append((row.sent_at, {
                        "subject": row.subject or "",
                        "body": row.body or "",
                        "tone": row.tone or tone,
                    }))

            # Source 2 — operator training samples (migration 0012).
            training_rows = session.execute(
                select(VoiceTrainingSample)
                .where(VoiceTrainingSample.user_id == user.id)
                .where(VoiceTrainingSample.sequence_index == seq)
                .where(VoiceTrainingSample.tone == tone)
                .order_by(VoiceTrainingSample.created_at.desc())
                .limit(VOICE_SAMPLE_WINDOW)
            ).scalars().all()
            for row in training_rows:
                candidates.append((row.created_at, {
                    "subject": row.subject or "",
                    "body": row.body or "",
                    "tone": row.tone or tone,
                }))

            # Merge — newest first, cap at window size. Tuples sort by
            # their first element (the timestamp); None timestamps sort
            # last by wrapping with a sentinel.
            import datetime as _dt
            candidates.sort(
                key=lambda t: t[0] or _dt.datetime.min,
                reverse=True,
            )
            out[tone][seq] = [sample for _, sample in candidates[:VOICE_SAMPLE_WINDOW]]

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

    ``voice_samples_by_tone`` shape:
      ``{tone: {sequence_index: [sample, ...]}}``
    Strict per-tone matching — the resolver renders each tone's
    samples in its own prompt section so the model only imitates
    within the right tone.
    """
    if user is None:
        return None
    return {
        "user_id": user.id,
        "display_name": user.display_name or "",
        "email": user.email or "",
        "tone_prompts": load_tone_prompts(session, user),
        "voice_samples_by_tone": load_voice_samples(session, user),
    }
