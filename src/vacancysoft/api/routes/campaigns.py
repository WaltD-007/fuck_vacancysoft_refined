"""Campaigns, dossiers, and agency-exclusion endpoints.

Covers the intelligence / outreach lifecycle:

  POST /api/agency                                    — mark a company as
                                                       a recruitment agency
                                                       (exclusion + cascade
                                                       delete of enriched
                                                       jobs / dossiers /
                                                       campaigns / queue
                                                       items)
  POST /api/leads/{item_id}/dossier                   — generate or return
                                                       an existing dossier
  GET  /api/leads/{item_id}/dossier                   — retrieve an
                                                       existing dossier
  POST /api/leads/{item_id}/campaign                  — generate outreach
                                                       emails (cached or
                                                       regenerated)
  POST /api/campaigns/{campaign_output_id}/launch     — schedule the 5-email
                                                       sequence for a tone
                                                       via Graph
  POST /api/campaigns/{campaign_output_id}/cancel     — cancel all pending
                                                       sends in a sequence

Extracted from `api/server.py` during the Week 4 split. Launch and cancel
are the canary delta — see .claude/plans/handoff-messaging-and-campaigns-
phase1.md for how this fits the broader plan.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from vacancysoft.api.schemas import (
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

    with SessionLocal() as s:
        # Match by enriched_job.team (post-extraction employer) OR by source employer name
        team_ej_ids = {
            row.id for row in s.execute(
                select(EnrichedJob).where(func.lower(EnrichedJob.team) == norm)
            ).scalars()
        }
        source_ids = [
            r.id for r in s.execute(
                select(Source).where(func.lower(Source.employer_name) == norm)
            ).scalars()
        ]
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


def _extract_sequence_for_tone(
    outreach_emails_blob, tone: str
) -> list[dict[str, str]]:
    """Pull the 5 ``{subject, body}`` pairs for ``tone`` out of a
    CampaignOutput.outreach_emails JSON blob.

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
    for item in emails_list:
        if not isinstance(item, dict):
            continue
        variants = item.get("variants") or {}
        v = variants.get(tone) if isinstance(variants, dict) else None
        if isinstance(v, dict):
            out.append({"subject": str(v.get("subject") or ""),
                        "body": str(v.get("body") or "")})
        elif item.get("subject") or item.get("body"):
            # Legacy single-tone shape (pre-2026-04-17 prompt rewrite).
            # Use it only if the requested tone is the step's stored tone,
            # else surface as empty so validation fails downstream.
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

    tone = (payload.tone or "").strip().lower()
    if tone not in _VALID_TONES:
        raise HTTPException(
            status_code=422,
            detail=f"invalid tone {payload.tone!r}; expected one of "
                   f"{sorted(_VALID_TONES)}",
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

        # 5 {subject, body} for the requested tone
        sequence = _extract_sequence_for_tone(campaign.outreach_emails, tone)
        if len(sequence) != 5:
            raise HTTPException(
                status_code=422,
                detail=f"campaign has {len(sequence)} emails for tone "
                       f"{tone!r}, expected 5 — regenerate the campaign",
            )
        for i, em in enumerate(sequence, start=1):
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
            tone=tone,
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
