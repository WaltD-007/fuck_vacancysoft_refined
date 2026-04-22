"""Campaigns, dossiers, and agency-exclusion endpoints.

Covers the intelligence / outreach lifecycle:

  POST /api/agency                        — mark a company as a recruitment
                                           agency (exclusion + cascade delete
                                           of enriched jobs / dossiers /
                                           campaigns / queue items for that
                                           company)
  POST /api/leads/{item_id}/dossier       — generate or return an existing
                                           intelligence dossier for a queued
                                           lead
  GET  /api/leads/{item_id}/dossier       — retrieve an existing dossier
  POST /api/leads/{item_id}/campaign      — generate outreach emails from an
                                           existing dossier

Extracted verbatim from `api/server.py` during the Week 4 split.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from vacancysoft.api.schemas import MarkAgencyRequest, MarkAgencyResponse, dossier_to_dict
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source


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
