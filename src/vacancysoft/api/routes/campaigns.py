"""Campaigns, dossiers, and agency-exclusion endpoints.

Covers the intelligence / outreach lifecycle:

  POST /api/agency                                    — mark a company as
                                                       a recruitment agency
  POST /api/leads/{item_id}/dossier                   — generate dossier
  GET  /api/leads/{item_id}/dossier                   — retrieve dossier
  POST /api/leads/{item_id}/campaign                  — generate emails
  POST /api/campaigns/{campaign_output_id}/launch     — schedule 5 sends
  POST /api/campaigns/{campaign_output_id}/cancel     — cancel pending
  GET  /api/campaigns                                 — paginated list
                                                       (PR P8 tracker)
  GET  /api/campaigns/launchers                       — distinct senders
                                                       for the dropdown
  GET  /api/campaigns/{id}/detail                     — per-step timeline
                                                       + reply log

Extracted from `api/server.py` during the Week 4 split. Launch and cancel
are the canary delta. The list/detail/launchers endpoints are PR P8 —
see .claude/plans/handoff-messaging-and-campaigns-phase1.md.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select

from vacancysoft.api.schemas import (
    ArchiveCampaignResponse,
    CampaignClickDetail,
    CampaignCounts,
    CampaignDetailResponse,
    CampaignHmInfo,
    CampaignLauncher,
    CampaignLaunchersResponse,
    CampaignListItem,
    CampaignListResponse,
    CampaignOpenDetail,
    CampaignReply,
    CampaignSenderInfo,
    CampaignSequenceStep,
    CampaignStageInfo,
    CancelCampaignResponse,
    LaunchCampaignRequest,
    LaunchCampaignResponse,
    MarkAgencyRequest,
    MarkAgencyResponse,
    dossier_to_dict,
)
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source

_VALID_TONES = frozenset(
    {"formal", "informal", "consultative", "direct", "candidate_spec", "technical"}
)

# Allowed values on the `?status=` query param of GET /api/campaigns. Any
# other value is rejected with 422 — a hard fail rather than silent
# pass-through so a typo doesn't return "no results, must be working".
_VALID_LIST_STATUSES = frozenset(
    {"replied", "opened", "sent", "pending", "cancelled", "failed", "no_response"}
)

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200


router = APIRouter(tags=["campaigns"])


# ── Mark company as agency ──


@router.post("/api/agency", response_model=MarkAgencyResponse)
def mark_agency(payload: MarkAgencyRequest):
    """Mark a company as a recruitment agency.

    Appends the company name to configs/agency_exclusions.yaml and
    hard-deletes every EnrichedJob (plus dependent dossiers, campaigns,
    queue items, scores, classifications) for that company. Leaves
    RawJob and Source rows intact.
    """
    from sqlalchemy import delete as sa_delete
    from vacancysoft.db.models import (
        ClassificationResult, IntelligenceDossier, CampaignOutput,
        ReviewQueueItem,
    )
    from vacancysoft.enrichers.recruiter_filter import add_agency_exclusion

    company = (payload.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    norm = company.lower()

    added = add_agency_exclusion(company)

    # Match by token subset, not exact lowercase string. Mirrors the
    # is_recruiter() rule shipped in PR #97 — clicking 'Korn Ferry'
    # must clean up 'Korn Ferry International', 'Korn Ferry UK Ltd',
    # 'Lorien Impellam' (when the YAML entry is 'impellam'), etc.
    # Without this, the click adds the entry to the YAML (so future
    # enrichments are blocked) but the historical decorated variants
    # survive and keep showing on the Live Feed across reboots.
    import re as _re
    def _alphanum_tokens(s: str | None) -> set[str]:
        return set(_re.findall(r"[a-z0-9]+", (s or "").lower()))

    entry_tokens = _alphanum_tokens(norm)

    with SessionLocal() as s:
        team_ej_ids: set[str] = set()
        source_ids: list[int] = []

        if entry_tokens:
            # EnrichedJobs whose team tokens are a superset of the entry
            # tokens. Pull only id+team so we can scan in Python without
            # loading row objects.
            for ej_id, team in s.execute(
                select(EnrichedJob.id, EnrichedJob.team).where(EnrichedJob.team.is_not(None))
            ).all():
                if entry_tokens <= _alphanum_tokens(team):
                    team_ej_ids.add(ej_id)

            # Sources whose employer_name token-matches.
            for src_id, emp in s.execute(
                select(Source.id, Source.employer_name).where(Source.employer_name.is_not(None))
            ).all():
                if entry_tokens <= _alphanum_tokens(emp):
                    source_ids.append(src_id)

        if source_ids:
            raw_ids = [
                r.id for r in s.execute(
                    select(RawJob).where(RawJob.source_id.in_(source_ids))
                ).scalars()
            ]
            if raw_ids:
                src_ej_ids = {
                    e.id for e in s.execute(
                        select(EnrichedJob).where(EnrichedJob.raw_job_id.in_(raw_ids))
                    ).scalars()
                }
                team_ej_ids |= src_ej_ids

        ej_ids = list(team_ej_ids)
        deleted_dossiers = 0
        deleted_queue = 0
        deleted_scores = 0
        deleted_classifications = 0
        deleted_jobs = 0

        if ej_ids:
            dossier_ids = [
                d.id for d in s.execute(
                    select(IntelligenceDossier).where(
                        IntelligenceDossier.enriched_job_id.in_(ej_ids)
                    )
                ).scalars()
            ]
            if dossier_ids:
                s.execute(sa_delete(CampaignOutput).where(CampaignOutput.dossier_id.in_(dossier_ids)))
            deleted_dossiers = s.execute(
                sa_delete(IntelligenceDossier).where(IntelligenceDossier.enriched_job_id.in_(ej_ids))
            ).rowcount or 0
            deleted_queue = s.execute(
                sa_delete(ReviewQueueItem).where(ReviewQueueItem.enriched_job_id.in_(ej_ids))
            ).rowcount or 0
            deleted_scores = s.execute(
                sa_delete(ScoreResult).where(ScoreResult.enriched_job_id.in_(ej_ids))
            ).rowcount or 0
            deleted_classifications = s.execute(
                sa_delete(ClassificationResult).where(ClassificationResult.enriched_job_id.in_(ej_ids))
            ).rowcount or 0
            deleted_jobs = s.execute(
                sa_delete(EnrichedJob).where(EnrichedJob.id.in_(ej_ids))
            ).rowcount or 0
        s.commit()

    # Marking an agency strips leads from every dashboard total, so both
    # the dashboard payload and the ledger/sources cache need to drop.
    from vacancysoft.api.ledger import clear_ledger_caches
    from vacancysoft.api.routes.leads import clear_dashboard_cache
    clear_ledger_caches()
    clear_dashboard_cache()

    return MarkAgencyResponse(
        added=added,
        deleted_jobs=deleted_jobs,
        deleted_classifications=deleted_classifications,
        deleted_scores=deleted_scores,
        deleted_dossiers=deleted_dossiers,
        deleted_queue_items=deleted_queue,
    )


# ── Intelligence Dossier ──


@router.post("/api/leads/{item_id}/dossier")
async def generate_lead_dossier(item_id: str):
    """Generate an intelligence dossier for a queued lead.

    Finds the enriched job, runs the dossier prompt through ChatGPT,
    and returns the structured dossier. If a dossier already exists,
    returns the existing one.
    """
    from vacancysoft.db.models import (
        IntelligenceDossier, ReviewQueueItem,
    )

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Enriched job is named directly on the queue item.
        enriched = s.execute(
            select(EnrichedJob).where(EnrichedJob.id == item.enriched_job_id)
        ).scalar_one_or_none()
        if not enriched:
            raise HTTPException(
                status_code=404,
                detail="No enriched job found for this queue item. Run the pipeline first.",
            )

        # Check for existing dossier
        existing = s.execute(
            select(IntelligenceDossier)
            .where(IntelligenceDossier.enriched_job_id == enriched.id)
            .order_by(IntelligenceDossier.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if existing:
            return dossier_to_dict(existing)

        # Generate new dossier
        from vacancysoft.intelligence.dossier import generate_dossier
        dossier = await generate_dossier(enriched.id, s)
        return dossier_to_dict(dossier)


@router.get("/api/leads/{item_id}/dossier")
def get_lead_dossier(item_id: str):
    """Retrieve an existing dossier for a queued lead."""
    from vacancysoft.db.models import IntelligenceDossier, ReviewQueueItem

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Enriched job is named directly on the queue item.
        enriched = s.execute(
            select(EnrichedJob).where(EnrichedJob.id == item.enriched_job_id)
        ).scalar_one_or_none()
        if not enriched:
            return JSONResponse(status_code=404, content={"detail": "No enriched job found"})

        dossier = s.execute(
            select(IntelligenceDossier)
            .where(IntelligenceDossier.enriched_job_id == enriched.id)
            .order_by(IntelligenceDossier.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not dossier:
            return JSONResponse(status_code=404, content={"detail": "No dossier generated yet"})

        return dossier_to_dict(dossier)


@router.post("/api/leads/{item_id}/campaign")
async def generate_lead_campaign(
    item_id: str,
    request: Request,
    regenerate: bool = False,
):
    """Generate (or return cached) campaign outreach emails for a lead.

    Two modes:
      * default (``regenerate=False``): returns the cached campaign if
        one exists. Never calls the LLM. Runs operator-agnostic — no
        user identity resolution. Matches the worker's pre-gen
        behaviour for byte-identical output.
      * ``regenerate=True``: bypasses the cache, resolves the current
        operator, loads their authored tone prompts + last-5-sent
        voice samples, and passes them to the resolver so the LLM
        renders a voice-aware campaign. Falls back to operator-
        agnostic generation if no user can be resolved (single-user-
        mode empty, missing header, 401/404 on the resolver) — still
        produces output, just without the voice layer.
    """
    from vacancysoft.api.auth import get_current_user
    from vacancysoft.db.models import CampaignOutput, IntelligenceDossier, ReviewQueueItem
    from vacancysoft.intelligence.voice import build_user_context

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        # Enriched job is named directly on the queue item (every
        # ReviewQueueItem creator populates enriched_job_id). No URL /
        # title fuzzy match — that path broke for text-pastes where
        # url=NULL and Source.employer_name is the "(Manual paste)"
        # placeholder.
        enriched = s.execute(
            select(EnrichedJob).where(EnrichedJob.id == item.enriched_job_id)
        ).scalar_one_or_none()

        if not enriched:
            raise HTTPException(status_code=404, detail="No enriched job found")

        dossier = s.execute(
            select(IntelligenceDossier)
            .where(IntelligenceDossier.enriched_job_id == enriched.id)
            .order_by(IntelligenceDossier.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not dossier:
            raise HTTPException(status_code=400, detail="Generate a dossier first before creating a campaign")

        # Cache-hit path — skip LLM, no user lookup, behaviour
        # identical to pre-voice-layer.
        if not regenerate:
            existing = s.execute(
                select(CampaignOutput)
                .where(CampaignOutput.dossier_id == dossier.id)
                .order_by(CampaignOutput.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if existing:
                return {
                    "id": existing.id,
                    "emails": existing.outreach_emails or [],
                    "model": existing.model_used,
                    "tokens": existing.tokens_used,
                    "tokens_prompt": existing.tokens_prompt,
                    "tokens_completion": existing.tokens_completion,
                    "cost_usd": existing.cost_usd,
                    "latency_ms": existing.latency_ms,
                }

        # Regenerate path OR no cached campaign yet — try to resolve
        # the operator's identity. Missing/ambiguous identity is NOT
        # a hard error here: we degrade gracefully to operator-
        # agnostic generation so a missing user header never blocks
        # the /campaigns page from rendering anything.
        user_context = None
        try:
            user = get_current_user(request, s)
            user_context = build_user_context(s, user)
        except HTTPException:
            user_context = None

        from vacancysoft.intelligence.campaign import generate_campaign
        campaign = await generate_campaign(
            dossier.id,
            s,
            force=regenerate,
            user_context=user_context,
        )
        return {
            "id": campaign.id,
            "emails": campaign.outreach_emails or [],
            "model": campaign.model_used,
            "tokens": campaign.tokens_used,
            "tokens_prompt": campaign.tokens_prompt,
            "tokens_completion": campaign.tokens_completion,
            "cost_usd": campaign.cost_usd,
            "latency_ms": campaign.latency_ms,
        }


# ── Outreach: launch + cancel ──────────────────────────────────────────


def _extract_sequence_for_tones(
    outreach_emails_blob, tones: list[str],
) -> list[dict[str, str]]:
    """Pull 5 ``{subject, body}`` pairs from ``outreach_emails``, one per
    step, each from its own selected tone (``tones[i]`` for step i).

    The stored shape has drifted across PRs — sometimes the JSON is the
    ``[{sequence, variants:{...}}, ...]`` list directly, sometimes wrapped
    as ``{"emails": [...]}``. Tolerate both. Returns the list (length 5
    in the happy path; caller validates).
    """
    if isinstance(outreach_emails_blob, list):
        emails_list = outreach_emails_blob
    elif isinstance(outreach_emails_blob, dict):
        emails_list = outreach_emails_blob.get("emails") or []
    else:
        emails_list = []

    out: list[dict[str, str]] = []
    for i, item in enumerate(emails_list):
        if i >= len(tones):
            break
        tone = tones[i]
        if not isinstance(item, dict):
            out.append({"subject": "", "body": ""})
            continue
        variants = item.get("variants") or {}
        v = variants.get(tone) if isinstance(variants, dict) else None
        if isinstance(v, dict):
            out.append({"subject": str(v.get("subject") or ""),
                        "body": str(v.get("body") or "")})
        elif item.get("subject") or item.get("body"):
            # Legacy single-tone shape (pre-2026-04-17 prompt rewrite).
            # Use it only if the requested tone is the step's stored tone.
            if (item.get("tone") or "").lower() == tone.lower():
                out.append({"subject": str(item.get("subject") or ""),
                            "body": str(item.get("body") or "")})
            else:
                out.append({"subject": "", "body": ""})
        else:
            out.append({"subject": "", "body": ""})
    return out


def _resolve_recipient_from_dossier(dossier) -> str:
    """Return the highest-priority hiring-manager email from a dossier,
    or empty string if none. ``hiring_managers`` is JSON — typed dict in
    the model but in practice a list of HM dicts."""
    if dossier is None:
        return ""
    hms = dossier.hiring_managers or []
    if isinstance(hms, dict):
        # Some early dossiers stored a single HM as a dict instead of a
        # list-of-dicts. Normalise.
        hms = [hms]
    if not isinstance(hms, list):
        return ""
    for hm in hms:
        if not isinstance(hm, dict):
            continue
        email = (hm.get("email") or "").strip().lower()
        if email and "@" in email:
            return email
    return ""


@router.post(
    "/api/campaigns/{campaign_output_id}/launch",
    response_model=LaunchCampaignResponse,
)
async def launch_campaign(
    campaign_output_id: str,
    request: Request,
    payload: LaunchCampaignRequest,
):
    """Schedule the 5-email outreach sequence for one campaign + tone.

    Resolves the operator's identity (via the same `get_current_user`
    every other authenticated route uses), pulls the 5 ``{subject, body}``
    pairs for the requested tone out of ``CampaignOutput.outreach_emails``,
    resolves the recipient (request body override or dossier HM), then
    delegates to :func:`schedule_outreach_sequence` which creates 5
    ``SentMessage`` rows + deferred ARQ jobs.

    Safe in dry-run (``OUTREACH_DRY_RUN=true``): the worker uses the
    canned Graph path on each fire; DB rows still transition through the
    full lifecycle so the UI / Campaigns page works end-to-end without
    any real mail leaving.
    """
    from vacancysoft.api.auth import get_current_user
    from vacancysoft.db.models import (
        CampaignOutput, IntelligenceDossier, SentMessage,
    )
    from vacancysoft.worker.outreach_tasks import schedule_outreach_sequence

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        # No ARQ pool means deferred jobs can't be enqueued. Better to
        # 503 cleanly than to write half a sequence then fail.
        raise HTTPException(
            status_code=503,
            detail="background queue unavailable; start the ARQ worker",
        )

    # Tone resolution: per-step ``tones`` is the canonical input from
    # the Builder (each step has its own dropdown). ``tone`` is the
    # legacy broadcast shape — applied to all 5 steps when present
    # alone. At least one form must be supplied.
    tones_list: list[str]
    if payload.tones is not None:
        if len(payload.tones) != 5:
            raise HTTPException(
                status_code=422,
                detail=f"tones must be a length-5 list (got {len(payload.tones)})",
            )
        normalised = [str(t or "").strip().lower() for t in payload.tones]
        for t in normalised:
            if t not in _VALID_TONES:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid tone {t!r} in tones list; expected one of "
                           f"{sorted(_VALID_TONES)}",
                )
        tones_list = normalised
    elif payload.tone is not None:
        tone = payload.tone.strip().lower()
        if tone not in _VALID_TONES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid tone {payload.tone!r}; expected one of "
                       f"{sorted(_VALID_TONES)}",
            )
        tones_list = [tone] * 5
    else:
        raise HTTPException(
            status_code=422,
            detail="either `tones` (per-step) or `tone` (broadcast) must be supplied",
        )

    cadence = payload.cadence_days
    if cadence is not None:
        if len(cadence) != 5 or cadence[0] != 0:
            raise HTTPException(
                status_code=422,
                detail="cadence_days must be a length-5 list starting with 0",
            )

    with SessionLocal() as s:
        campaign = s.execute(
            select(CampaignOutput).where(CampaignOutput.id == campaign_output_id)
        ).scalar_one_or_none()
        if campaign is None:
            raise HTTPException(status_code=404, detail="campaign not found")

        # Operator identity. Falls through to single-user-mode in dev
        # (matches the rest of /api/*). 401 here is a hard stop —
        # without an operator we have nothing to put in sender_user_id.
        try:
            user = get_current_user(request, s)
        except HTTPException:
            raise HTTPException(
                status_code=401,
                detail="no operator identity; bootstrap a user with "
                       "`prospero user add` then retry",
            )
        sender_user_id = (user.entra_object_id or user.email or "").strip()
        if not sender_user_id:
            raise HTTPException(
                status_code=422,
                detail="resolved user has no entra_object_id or email",
            )

        # 5 {subject, body} pairs, one per step, each from its own tone.
        sequence = _extract_sequence_for_tones(campaign.outreach_emails, tones_list)
        if len(sequence) != 5:
            raise HTTPException(
                status_code=422,
                detail=f"campaign has {len(sequence)} emails (expected 5) — "
                       "regenerate the campaign",
            )
        for i, (em, tone) in enumerate(zip(sequence, tones_list), start=1):
            if not em["subject"].strip() or not em["body"].strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"sequence step {i} for tone {tone!r} is missing "
                           f"subject or body",
                )

        # Recipient: explicit override > dossier HM > 422
        recipient = (payload.recipient_email or "").strip().lower()
        if not recipient:
            dossier = s.execute(
                select(IntelligenceDossier)
                .where(IntelligenceDossier.id == campaign.dossier_id)
            ).scalar_one_or_none()
            recipient = _resolve_recipient_from_dossier(dossier)
        if not recipient or "@" not in recipient:
            raise HTTPException(
                status_code=422,
                detail="no recipient_email supplied and no usable hiring "
                       "manager email on the dossier",
            )

        sent_message_ids = await schedule_outreach_sequence(
            redis=redis,
            session=s,
            campaign_output_id=campaign.id,
            sender_user_id=sender_user_id,
            recipient_email=recipient,
            recipient_name=payload.recipient_name,
            tones=tones_list,
            emails=sequence,
            cadence_days=cadence,
        )

        first_send = s.execute(
            select(SentMessage).where(SentMessage.id == sent_message_ids[0])
        ).scalar_one_or_none()
        first_at = (
            first_send.scheduled_for.isoformat() + "Z"
            if first_send and first_send.scheduled_for else None
        )

    return LaunchCampaignResponse(
        status="scheduled",
        sent_message_ids=sent_message_ids,
        first_send_scheduled_for=first_at,
    )


@router.post(
    "/api/campaigns/{campaign_output_id}/cancel",
    response_model=CancelCampaignResponse,
)
async def cancel_campaign(campaign_output_id: str, request: Request):
    """Cancel every still-pending send in a sequence.

    Wraps :func:`cancel_pending_sequence_manual`. Returns 0 if nothing
    was pending (idempotent — calling twice doesn't error). Idempotent
    by design so the UI can retry safely on flaky networks.
    """
    from vacancysoft.db.models import CampaignOutput
    from vacancysoft.worker.outreach_tasks import cancel_pending_sequence_manual

    redis = getattr(request.app.state, "redis", None)
    # If Redis is gone we can still flip statuses in DB — the deferred
    # jobs would fire and immediately exit ('not pending') anyway. Don't
    # block the cancel just because the queue's offline.

    with SessionLocal() as s:
        exists = s.execute(
            select(CampaignOutput.id)
            .where(CampaignOutput.id == campaign_output_id)
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="campaign not found")

        cancelled = await cancel_pending_sequence_manual(
            session=s,
            redis=redis,
            campaign_output_id=campaign_output_id,
        )

    return CancelCampaignResponse(cancelled_count=cancelled)


# ── PR P8: Campaigns tracker ──────────────────────────────────────────


def _hm_name_from_dossier(hiring_managers: Any, recipient_email: str | None) -> str | None:
    """Best-match HM display name from the dossier's JSON column.

    The dossier's ``hiring_managers`` is JSON — typed dict-or-None on
    the model but in practice a list of dicts (occasionally a single
    dict on legacy rows). Match by ``email`` first; fall back to the
    first entry that has a usable name.
    """
    if not hiring_managers:
        return None
    if isinstance(hiring_managers, dict):
        # Legacy single-HM shape — coerce to list-of-one.
        hiring_managers = [hiring_managers]
    if not isinstance(hiring_managers, list):
        return None
    needle = (recipient_email or "").strip().lower()
    fallback: str | None = None
    for hm in hiring_managers:
        if not isinstance(hm, dict):
            continue
        hm_email = (hm.get("email") or "").strip().lower()
        hm_name = (hm.get("name") or "").strip()
        if not hm_name:
            continue
        if needle and hm_email == needle:
            return hm_name
        if fallback is None:
            fallback = hm_name
    return fallback


def _derive_status(
    *,
    pending: int,
    sent: int,
    cancelled_manual: int,
    cancelled_replied: int,
    failed: int,
    reply_count: int,
    open_count: int,
) -> str:
    """Compute the single-status chip from per-status counts.

    Priority (highest wins):
      replied > opened > cancelled > failed > sent > pending

    ``cancelled_replied`` collapses into ``replied`` because the user-
    visible signal is "they replied", not "we cancelled".
    """
    if reply_count > 0 or cancelled_replied > 0:
        return "replied"
    if cancelled_manual > 0 and pending == 0:
        return "cancelled"
    if open_count > 0:
        return "opened"
    if failed > 0 and sent == 0:
        return "failed"
    if sent > 0:
        return "sent"
    return "pending"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _resolve_user(session, sender_user_id: str | None) -> tuple[str | None, str | None]:
    """Look up display_name + email for a sender_user_id (OID or email).

    Returns (display_name, email) — either or both may be None when the
    user row doesn't exist (pre-SSO header path / deleted user).
    """
    from vacancysoft.db.models import User
    if not sender_user_id:
        return None, None
    u = session.execute(
        select(User).where(
            or_(
                User.entra_object_id == sender_user_id,
                User.email == sender_user_id.lower(),
            )
        )
    ).scalar_one_or_none()
    if u is None:
        return None, None
    return (u.display_name or None), (u.email or None)


@router.get("/api/campaigns/launchers", response_model=CampaignLaunchersResponse)
def list_campaign_launchers():
    """Distinct operators who have launched at least one campaign.

    Drives the user-filter dropdown on the Campaigns page. Returns one
    row per distinct ``sent_messages.sender_user_id``, joined to the
    ``users`` table for display_name + email. Sender IDs with no
    matching user row still appear (with ``display_name=None``) so the
    operator can still filter to those campaigns — better than
    silently dropping them.
    """
    from vacancysoft.db.models import SentMessage, User

    with SessionLocal() as s:
        rows = s.execute(
            select(
                SentMessage.sender_user_id,
                func.count(func.distinct(SentMessage.campaign_output_id)).label("campaign_count"),
            )
            .group_by(SentMessage.sender_user_id)
            .order_by(func.count(func.distinct(SentMessage.campaign_output_id)).desc())
        ).all()

        # Resolve each sender to a User row (may not exist yet).
        sender_ids = [r.sender_user_id for r in rows if r.sender_user_id]
        if sender_ids:
            users = s.execute(
                select(User).where(
                    or_(
                        User.entra_object_id.in_(sender_ids),
                        User.email.in_([sid.lower() for sid in sender_ids]),
                    )
                )
            ).scalars().all()
        else:
            users = []
        # Build a map keyed by both possible matching values.
        user_map: dict[str, User] = {}
        for u in users:
            if u.entra_object_id:
                user_map[u.entra_object_id] = u
            if u.email:
                user_map[u.email.lower()] = u

        launchers = []
        for r in rows:
            sid = r.sender_user_id or ""
            u = user_map.get(sid) or user_map.get(sid.lower())
            launchers.append(CampaignLauncher(
                sender_user_id=sid,
                display_name=(u.display_name if u else None),
                email=(u.email if u else None),
                campaign_count=int(r.campaign_count),
            ))

    return CampaignLaunchersResponse(launchers=launchers)


@router.get("/api/campaigns", response_model=CampaignListResponse)
def list_campaigns(
    status: str | None = Query(default=None, description=f"One of: {sorted(_VALID_LIST_STATUSES)}"),
    owner: str | None = Query(default=None, description="Filter to a specific sender_user_id (OID or email)"),
    since: str | None = Query(default=None, description="ISO-8601; only campaigns with activity after this"),
    archived: str = Query(default="false", description="false (default — hide archived) | true (only archived) | all"),
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
):
    """One row per ``CampaignOutput`` with at least one ``SentMessage``.

    Aggregates opens / clicks / replies across all SentMessages in the
    campaign. ``opens`` excludes ``likely_apple_mpp`` events;
    ``clicks`` excludes ``likely_scanner`` events. Both are visible
    individually on the detail view.

    Status filter values:
      * ``replied`` — at least one received_reply OR a cancelled_replied row
      * ``opened`` — at least one open event, no reply
      * ``sent`` — at least one sent, no opens / replies
      * ``pending`` — only pending rows
      * ``cancelled`` — manual cancel (no replies)
      * ``failed`` — at least one failed, none sent
      * ``no_response`` — all 5 sent and none of replies / opens / clicks

    Archive filter (``?archived=``):
      * ``false`` (default) — hide archived rows
      * ``true``            — show only archived rows
      * ``all``             — show both

    Sort: last_activity DESC NULLS LAST.
    """
    from vacancysoft.db.models import (
        CampaignOutput,
        ClassificationResult,
        ClickEvent,
        IntelligenceDossier,
        OpenEvent,
        ReceivedReply,
        SentMessage,
    )

    if status is not None and status not in _VALID_LIST_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"invalid status {status!r}; expected one of {sorted(_VALID_LIST_STATUSES)}",
        )

    archived_norm = (archived or "false").strip().lower()
    if archived_norm not in {"false", "true", "all"}:
        raise HTTPException(
            status_code=422,
            detail=f"invalid archived={archived!r}; expected false | true | all",
        )

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"invalid since timestamp: {since!r}",
            )

    with SessionLocal() as s:
        # Step 1: per-campaign sent-status aggregates.
        sm_stats = s.execute(
            select(
                SentMessage.campaign_output_id.label("co_id"),
                func.min(SentMessage.recipient_email).label("recipient_email"),
                # All 5 SentMessage rows in a sequence share the same
                # recipient_name. MIN gives a deterministic value when
                # all are equal, NULL when none are set.
                func.min(SentMessage.recipient_name).label("recipient_name"),
                func.min(SentMessage.sender_user_id).label("sender_user_id"),
                func.min(SentMessage.created_at).label("launched_at"),
                func.max(SentMessage.sent_at).label("last_sent"),
                func.sum(case((SentMessage.status == "sent", 1), else_=0)).label("sent"),
                func.sum(case((SentMessage.status == "pending", 1), else_=0)).label("pending"),
                func.sum(case((SentMessage.status == "cancelled_manual", 1), else_=0)).label("cancelled_manual"),
                func.sum(case((SentMessage.status == "cancelled_replied", 1), else_=0)).label("cancelled_replied"),
                func.sum(case((SentMessage.status == "failed", 1), else_=0)).label("failed"),
                func.count(SentMessage.id).label("total"),
            )
            .group_by(SentMessage.campaign_output_id)
        ).all()

        if not sm_stats:
            return CampaignListResponse(items=[], total=0, limit=limit, offset=offset)

        co_ids = [r.co_id for r in sm_stats]

        # Step 2: per-campaign open + click + reply aggregates.
        opens_by_co = dict(s.execute(
            select(
                SentMessage.campaign_output_id,
                func.count(OpenEvent.id).label("c"),
            )
            .join(OpenEvent, OpenEvent.sent_message_id == SentMessage.id)
            .where(SentMessage.campaign_output_id.in_(co_ids))
            .where(OpenEvent.likely_apple_mpp.is_(False))
            .group_by(SentMessage.campaign_output_id)
        ).all())
        last_open_by_co = dict(s.execute(
            select(
                SentMessage.campaign_output_id,
                func.max(OpenEvent.opened_at).label("ts"),
            )
            .join(OpenEvent, OpenEvent.sent_message_id == SentMessage.id)
            .where(SentMessage.campaign_output_id.in_(co_ids))
            .group_by(SentMessage.campaign_output_id)
        ).all())
        clicks_by_co = dict(s.execute(
            select(
                SentMessage.campaign_output_id,
                func.count(ClickEvent.id).label("c"),
            )
            .join(ClickEvent, ClickEvent.sent_message_id == SentMessage.id)
            .where(SentMessage.campaign_output_id.in_(co_ids))
            .where(ClickEvent.likely_scanner.is_(False))
            .group_by(SentMessage.campaign_output_id)
        ).all())
        last_click_by_co = dict(s.execute(
            select(
                SentMessage.campaign_output_id,
                func.max(ClickEvent.clicked_at).label("ts"),
            )
            .join(ClickEvent, ClickEvent.sent_message_id == SentMessage.id)
            .where(SentMessage.campaign_output_id.in_(co_ids))
            .group_by(SentMessage.campaign_output_id)
        ).all())

        # Replies: matched via conversation_id, not direct FK on
        # campaign_output_id. Group via SentMessage join.
        reply_rows = s.execute(
            select(
                SentMessage.campaign_output_id,
                ReceivedReply.received_at,
                ReceivedReply.id,
            )
            .join(ReceivedReply, ReceivedReply.conversation_id == SentMessage.conversation_id)
            .where(SentMessage.campaign_output_id.in_(co_ids))
            .where(SentMessage.conversation_id.is_not(None))
        ).all()
        reply_count_by_co: dict[str, int] = defaultdict(int)
        last_reply_by_co: dict[str, datetime] = {}
        seen: set[tuple[str, str]] = set()
        for r in reply_rows:
            key = (r.campaign_output_id, r.id)
            if key in seen:
                continue
            seen.add(key)
            reply_count_by_co[r.campaign_output_id] += 1
            cur = last_reply_by_co.get(r.campaign_output_id)
            if cur is None or (r.received_at and r.received_at > cur):
                last_reply_by_co[r.campaign_output_id] = r.received_at

        # Step 3: lead context (one query per chained join — small N).
        # Pull archived_at here too so the row-assembly loop can both
        # apply the ?archived= filter and surface the timestamp on
        # each list item.
        co_rows = s.execute(
            select(
                CampaignOutput.id.label("co_id"),
                CampaignOutput.dossier_id,
                CampaignOutput.archived_at,
                IntelligenceDossier.enriched_job_id,
                IntelligenceDossier.hiring_managers,
                EnrichedJob.title,
                EnrichedJob.team,
                EnrichedJob.location_city,
                EnrichedJob.location_country,
            )
            .join(IntelligenceDossier, IntelligenceDossier.id == CampaignOutput.dossier_id)
            .join(EnrichedJob, EnrichedJob.id == IntelligenceDossier.enriched_job_id)
            .where(CampaignOutput.id.in_(co_ids))
        ).all()
        co_ctx = {r.co_id: r for r in co_rows}

        # Step 4: category from classification (most-recent per enriched_job).
        ej_ids = [r.enriched_job_id for r in co_rows]
        cat_by_ej: dict[str, str | None] = {}
        if ej_ids:
            for cls in s.execute(
                select(
                    ClassificationResult.enriched_job_id,
                    ClassificationResult.primary_taxonomy_key,
                )
                .where(ClassificationResult.enriched_job_id.in_(ej_ids))
                .order_by(ClassificationResult.enriched_job_id, ClassificationResult.id.desc())
            ).all():
                if cls.enriched_job_id not in cat_by_ej:
                    cat_by_ej[cls.enriched_job_id] = cls.primary_taxonomy_key

        # Step 5: assemble + filter + sort + paginate in Python. Dataset
        # is small (one row per campaign_output, bounded by operator
        # activity) — no point pushing this into SQL for the canary.
        items: list[CampaignListItem] = []
        for r in sm_stats:
            ctx = co_ctx.get(r.co_id)
            if ctx is None:
                # Orphan campaign_output (rare; defensive).
                continue
            opens = int(opens_by_co.get(r.co_id, 0))
            clicks = int(clicks_by_co.get(r.co_id, 0))
            replies = int(reply_count_by_co.get(r.co_id, 0))

            # Archive filter (default hides archived rows).
            row_archived = ctx.archived_at is not None
            if archived_norm == "false" and row_archived:
                continue
            if archived_norm == "true" and not row_archived:
                continue

            # Owner filter
            sender_id = r.sender_user_id or ""
            if owner and owner.lower() != sender_id.lower():
                continue

            # Last activity = max across send/open/click/reply timestamps.
            candidates: list[datetime] = [
                t for t in [
                    r.last_sent,
                    last_open_by_co.get(r.co_id),
                    last_click_by_co.get(r.co_id),
                    last_reply_by_co.get(r.co_id),
                ] if t is not None
            ]
            last_activity = max(candidates) if candidates else None

            if since_dt is not None and (last_activity is None or last_activity < since_dt):
                continue

            derived = _derive_status(
                pending=int(r.pending),
                sent=int(r.sent),
                cancelled_manual=int(r.cancelled_manual),
                cancelled_replied=int(r.cancelled_replied),
                failed=int(r.failed),
                reply_count=replies,
                open_count=opens,
            )

            # `no_response` is a synthesised filter — true when the
            # whole sequence is sent (no pending, no failed, no replies,
            # no opens, no clicks). Don't fold into _derive_status's
            # priority; keep it as a filter-only label.
            if status == "no_response":
                if not (
                    int(r.sent) >= 1
                    and int(r.pending) == 0
                    and int(r.failed) == 0
                    and replies == 0
                    and opens == 0
                    and clicks == 0
                ):
                    continue
            elif status is not None and derived != status:
                continue

            display_name, email = _resolve_user(s, r.sender_user_id)
            # Operator-verified name (typed in Builder) wins over the
            # dossier-derived guess. Fall back to the dossier when the
            # verified name wasn't supplied at launch.
            verified_name = (r.recipient_name or "").strip() if r.recipient_name else ""
            hm_name = verified_name or _hm_name_from_dossier(
                ctx.hiring_managers, r.recipient_email,
            )

            items.append(CampaignListItem(
                campaign_output_id=r.co_id,
                title=ctx.title,
                company=ctx.team,
                location_city=ctx.location_city,
                location_country=ctx.location_country,
                category=cat_by_ej.get(ctx.enriched_job_id),
                hiring_manager=CampaignHmInfo(
                    email=r.recipient_email, name=hm_name,
                ),
                sender=CampaignSenderInfo(
                    sender_user_id=sender_id,
                    display_name=display_name,
                    email=email,
                ),
                stage=CampaignStageInfo(
                    sent=int(r.sent),
                    pending=int(r.pending),
                    cancelled=int(r.cancelled_manual) + int(r.cancelled_replied),
                    failed=int(r.failed),
                    total=int(r.total),
                ),
                status=derived,
                counts=CampaignCounts(opens=opens, clicks=clicks, replies=replies),
                last_activity=_iso(last_activity),
                launched_at=_iso(r.launched_at),
                archived_at=_iso(ctx.archived_at),
            ))

        # Sort + paginate.
        items.sort(
            key=lambda i: (i.last_activity or "", i.launched_at or ""),
            reverse=True,
        )
        total = len(items)
        page = items[offset : offset + limit]

    return CampaignListResponse(items=page, total=total, limit=limit, offset=offset)


@router.get(
    "/api/campaigns/{campaign_output_id}/detail",
    response_model=CampaignDetailResponse,
)
def get_campaign_detail(campaign_output_id: str):
    """Per-step timeline + reply log for one campaign.

    Used by the Campaigns slide-over. Returns every SentMessage with
    its individual open + click events (including scanner-flagged ones —
    UI greys them out) plus every ReceivedReply on the conversation.
    """
    from vacancysoft.db.models import (
        CampaignOutput,
        ClassificationResult,
        ClickEvent,
        IntelligenceDossier,
        OpenEvent,
        ReceivedReply,
        SentMessage,
    )

    with SessionLocal() as s:
        co = s.execute(
            select(CampaignOutput).where(CampaignOutput.id == campaign_output_id)
        ).scalar_one_or_none()
        if co is None:
            raise HTTPException(status_code=404, detail="campaign not found")

        dossier = s.execute(
            select(IntelligenceDossier).where(IntelligenceDossier.id == co.dossier_id)
        ).scalar_one_or_none()
        ej = None
        if dossier is not None:
            ej = s.execute(
                select(EnrichedJob).where(EnrichedJob.id == dossier.enriched_job_id)
            ).scalar_one_or_none()

        category = None
        if dossier is not None:
            cls = s.execute(
                select(ClassificationResult)
                .where(ClassificationResult.enriched_job_id == dossier.enriched_job_id)
                .order_by(ClassificationResult.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if cls is not None:
                category = cls.primary_taxonomy_key

        # All sent messages on this campaign, plus all events + replies.
        sms = s.execute(
            select(SentMessage)
            .where(SentMessage.campaign_output_id == campaign_output_id)
            .order_by(SentMessage.sequence_index)
        ).scalars().all()
        sm_ids = [sm.id for sm in sms]
        conversation_ids = list({sm.conversation_id for sm in sms if sm.conversation_id})

        opens_by_sm: dict[str, list[OpenEvent]] = defaultdict(list)
        if sm_ids:
            for ev in s.execute(
                select(OpenEvent)
                .where(OpenEvent.sent_message_id.in_(sm_ids))
                .order_by(OpenEvent.opened_at)
            ).scalars().all():
                opens_by_sm[ev.sent_message_id].append(ev)

        clicks_by_sm: dict[str, list[ClickEvent]] = defaultdict(list)
        if sm_ids:
            for ev in s.execute(
                select(ClickEvent)
                .where(ClickEvent.sent_message_id.in_(sm_ids))
                .order_by(ClickEvent.clicked_at)
            ).scalars().all():
                clicks_by_sm[ev.sent_message_id].append(ev)

        replies: list[ReceivedReply] = []
        if conversation_ids:
            replies = s.execute(
                select(ReceivedReply)
                .where(ReceivedReply.conversation_id.in_(conversation_ids))
                .order_by(ReceivedReply.received_at)
            ).scalars().all()

        # Counts (for the header — match list endpoint's exclusions).
        list_open_count = sum(
            1 for evs in opens_by_sm.values() for e in evs if not e.likely_apple_mpp
        )
        list_click_count = sum(
            1 for evs in clicks_by_sm.values() for e in evs if not e.likely_scanner
        )
        reply_count = len(replies)

        # Build steps.
        steps: list[CampaignSequenceStep] = []
        for sm in sms:
            steps.append(CampaignSequenceStep(
                sequence_index=sm.sequence_index,
                tone=sm.tone,
                status=sm.status,
                scheduled_for=_iso(sm.scheduled_for),
                sent_at=_iso(sm.sent_at),
                subject=sm.subject,
                error_message=sm.error_message,
                opens=[
                    CampaignOpenDetail(
                        opened_at=ev.opened_at.isoformat(),
                        user_agent=ev.user_agent,
                        likely_apple_mpp=bool(ev.likely_apple_mpp),
                    )
                    for ev in opens_by_sm.get(sm.id, [])
                ],
                clicks=[
                    CampaignClickDetail(
                        clicked_at=ev.clicked_at.isoformat(),
                        original_url=ev.original_url,
                        user_agent=ev.user_agent,
                        likely_scanner=bool(ev.likely_scanner),
                    )
                    for ev in clicks_by_sm.get(sm.id, [])
                ],
            ))

        # Aggregate sender + status for the header.
        sender_id = sms[0].sender_user_id if sms else ""
        recipient = sms[0].recipient_email if sms else None
        recipient_name_raw = sms[0].recipient_name if sms else None
        display_name, email = _resolve_user(s, sender_id)
        # Operator-verified name (typed at launch) wins over the
        # dossier-derived guess. Fall back when not supplied.
        verified_name = (recipient_name_raw or "").strip() if recipient_name_raw else ""
        hm_name = verified_name or _hm_name_from_dossier(
            dossier.hiring_managers if dossier is not None else None,
            recipient,
        )

        derived = _derive_status(
            pending=sum(1 for sm in sms if sm.status == "pending"),
            sent=sum(1 for sm in sms if sm.status == "sent"),
            cancelled_manual=sum(1 for sm in sms if sm.status == "cancelled_manual"),
            cancelled_replied=sum(1 for sm in sms if sm.status == "cancelled_replied"),
            failed=sum(1 for sm in sms if sm.status == "failed"),
            reply_count=reply_count,
            open_count=list_open_count,
        )

        launched_at = min((sm.created_at for sm in sms), default=None)
        candidates: list[datetime] = []
        for sm in sms:
            if sm.sent_at is not None:
                candidates.append(sm.sent_at)
        for evs in opens_by_sm.values():
            candidates.extend(e.opened_at for e in evs)
        for evs in clicks_by_sm.values():
            candidates.extend(e.clicked_at for e in evs)
        candidates.extend(r.received_at for r in replies if r.received_at is not None)
        last_activity = max(candidates) if candidates else None

    return CampaignDetailResponse(
        campaign_output_id=campaign_output_id,
        title=ej.title if ej is not None else None,
        company=ej.team if ej is not None else None,
        location_city=ej.location_city if ej is not None else None,
        location_country=ej.location_country if ej is not None else None,
        category=category,
        hiring_manager=CampaignHmInfo(email=recipient, name=hm_name),
        sender=CampaignSenderInfo(
            sender_user_id=sender_id, display_name=display_name, email=email,
        ),
        status=derived,
        counts=CampaignCounts(
            opens=list_open_count, clicks=list_click_count, replies=reply_count,
        ),
        launched_at=_iso(launched_at),
        last_activity=_iso(last_activity),
        archived_at=_iso(co.archived_at),
        steps=steps,
        replies=[
            CampaignReply(
                received_at=r.received_at.isoformat(),
                from_email=r.from_email,
                subject=r.subject,
            )
            for r in replies
        ],
    )


# ── Archive / unarchive ───────────────────────────────────────────


@router.post(
    "/api/campaigns/{campaign_output_id}/archive",
    response_model=ArchiveCampaignResponse,
)
def archive_campaign(campaign_output_id: str):
    """Soft-archive a campaign — hide it from the default list view.

    Refuses with 422 when there are still pending sends, so an archived
    campaign can never have deferred ARQ jobs firing in the background.
    Operator must hit Stop first (which cancels pending rows), then
    archive.

    Idempotent — archiving an already-archived row leaves the
    ``archived_at`` timestamp unchanged.
    """
    from vacancysoft.db.models import CampaignOutput, SentMessage

    with SessionLocal() as s:
        co = s.execute(
            select(CampaignOutput).where(CampaignOutput.id == campaign_output_id)
        ).scalar_one_or_none()
        if co is None:
            raise HTTPException(status_code=404, detail="campaign not found")

        pending_count = s.execute(
            select(func.count(SentMessage.id))
            .where(SentMessage.campaign_output_id == campaign_output_id)
            .where(SentMessage.status == "pending")
        ).scalar() or 0
        if pending_count > 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"cannot archive — {pending_count} pending send(s) "
                    "still scheduled. Stop the campaign first."
                ),
            )

        if co.archived_at is None:
            co.archived_at = datetime.utcnow()
            s.commit()
        return ArchiveCampaignResponse(archived_at=_iso(co.archived_at))


@router.post(
    "/api/campaigns/{campaign_output_id}/unarchive",
    response_model=ArchiveCampaignResponse,
)
def unarchive_campaign(campaign_output_id: str):
    """Restore an archived campaign to the default list view.

    Idempotent — unarchiving a non-archived row is a no-op (returns
    ``archived_at: null``).
    """
    from vacancysoft.db.models import CampaignOutput

    with SessionLocal() as s:
        co = s.execute(
            select(CampaignOutput).where(CampaignOutput.id == campaign_output_id)
        ).scalar_one_or_none()
        if co is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        if co.archived_at is not None:
            co.archived_at = None
            s.commit()
        return ArchiveCampaignResponse(archived_at=None)
