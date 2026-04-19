"""Prospero API — lightweight FastAPI server wrapping the scraping engine."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source
from vacancysoft.api.routes import add_company as add_company_routes
from vacancysoft.api.routes import leads as leads_routes
from vacancysoft.api.routes import sources as sources_routes
from vacancysoft.api.schemas import MarkAgencyRequest, MarkAgencyResponse

app = FastAPI(title="Prospero API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Per-topic route modules ──
app.include_router(leads_routes.router)
app.include_router(sources_routes.router)
app.include_router(add_company_routes.router)


# ── Redis connection for ARQ job queue ──
from arq import create_pool
from vacancysoft.worker.settings import _redis_settings

@app.on_event("startup")
async def _startup():
    try:
        app.state.redis = await create_pool(_redis_settings())
        # Re-enqueue any orphaned pending leads from previous server runs
        from vacancysoft.db.models import ReviewQueueItem
        with SessionLocal() as s:
            # Fix leads stuck in "generating" that already have dossiers
            from vacancysoft.db.models import IntelligenceDossier
            stuck = list(s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.status == "generating")
                .where(ReviewQueueItem.queue_type == "campaign")
                .where(ReviewQueueItem.enriched_job_id.in_(
                    select(IntelligenceDossier.enriched_job_id)
                ))
            ).scalars())
            for item in stuck:
                item.status = "ready"
            if stuck:
                s.commit()

            # Re-enqueue genuinely pending/stuck leads
            pending = list(s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.status.in_(("pending", "generating")))
                .where(ReviewQueueItem.queue_type == "campaign")
            ).scalars())
            for item in pending:
                ev = item.evidence_blob or {}
                await app.state.redis.enqueue_job("process_lead", item.id, ev.get("url"), ev.get("company"), ev.get("title"))
            if pending or stuck:
                import logging
                logging.getLogger(__name__).info("Startup: fixed %d stuck leads, re-enqueued %d pending", len(stuck), len(pending))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Redis not available, falling back to in-process tasks: %s", exc)
        app.state.redis = None

@app.on_event("shutdown")
async def _shutdown():
    if getattr(app.state, "redis", None):
        await app.state.redis.close()


# ── Models ──




# ── Mark company as agency ──


@app.post("/api/agency", response_model=MarkAgencyResponse)
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

    return MarkAgencyResponse(
        added=added,
        deleted_jobs=deleted_jobs,
        deleted_classifications=deleted_classifications,
        deleted_scores=deleted_scores,
        deleted_dossiers=deleted_dossiers,
        deleted_queue_items=deleted_queue,
    )


# ── Intelligence Dossier ──

@app.post("/api/leads/{item_id}/dossier")
async def generate_lead_dossier(item_id: str):
    """Generate an intelligence dossier for a queued lead.

    Finds the enriched job, runs the dossier prompt through ChatGPT,
    and returns the structured dossier. If a dossier already exists,
    returns the existing one.
    """
    from vacancysoft.db.models import (
        ReviewQueueItem, ClassificationResult, IntelligenceDossier,
    )

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        evidence = item.evidence_blob or {}
        url = evidence.get("url", "")
        title = evidence.get("title", "")
        company = evidence.get("company", "")

        # Find the enriched job by URL or title+company
        enriched = None
        if url:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .where(RawJob.discovered_url == url)
                .limit(1)
            ).scalar_one_or_none()

        if not enriched and title:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .join(Source, RawJob.source_id == Source.id)
                .where(EnrichedJob.title.ilike(f"%{title}%"))
                .where(Source.employer_name.ilike(f"%{company}%"))
                .limit(1)
            ).scalar_one_or_none()

        if not enriched:
            raise HTTPException(status_code=404, detail=f"No enriched job found for '{title}' at '{company}'. Run the pipeline first.")

        # Check for existing dossier
        existing = s.execute(
            select(IntelligenceDossier)
            .where(IntelligenceDossier.enriched_job_id == enriched.id)
            .order_by(IntelligenceDossier.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if existing:
            return _dossier_to_dict(existing)

        # Generate new dossier
        from vacancysoft.intelligence.dossier import generate_dossier
        dossier = await generate_dossier(enriched.id, s)
        return _dossier_to_dict(dossier)


@app.get("/api/leads/{item_id}/dossier")
def get_lead_dossier(item_id: str):
    """Retrieve an existing dossier for a queued lead."""
    from vacancysoft.db.models import ReviewQueueItem, IntelligenceDossier

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        evidence = item.evidence_blob or {}
        url = evidence.get("url", "")
        title = evidence.get("title", "")
        company = evidence.get("company", "")

        enriched = None
        if url:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .where(RawJob.discovered_url == url)
                .limit(1)
            ).scalar_one_or_none()

        if not enriched and title:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .join(Source, RawJob.source_id == Source.id)
                .where(EnrichedJob.title.ilike(f"%{title}%"))
                .where(Source.employer_name.ilike(f"%{company}%"))
                .limit(1)
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

        return _dossier_to_dict(dossier)


@app.post("/api/leads/{item_id}/campaign")
async def generate_lead_campaign(item_id: str):
    """Generate campaign outreach emails from an existing dossier."""
    from vacancysoft.db.models import ReviewQueueItem, IntelligenceDossier, CampaignOutput

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Queue item not found")

        evidence = item.evidence_blob or {}
        url = evidence.get("url", "")
        title = evidence.get("title", "")
        company = evidence.get("company", "")

        enriched = None
        if url:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .where(RawJob.discovered_url == url)
                .limit(1)
            ).scalar_one_or_none()

        if not enriched and title:
            enriched = s.execute(
                select(EnrichedJob)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .join(Source, RawJob.source_id == Source.id)
                .where(EnrichedJob.title.ilike(f"%{title}%"))
                .where(Source.employer_name.ilike(f"%{company}%"))
                .limit(1)
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

        # Check for existing campaign
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

        from vacancysoft.intelligence.campaign import generate_campaign
        campaign = await generate_campaign(dossier.id, s)
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


def _dossier_to_dict(d) -> dict:
    return {
        "id": d.id,
        "category": d.category_used,
        "model": d.model_used,
        "tokens": d.tokens_used,
        "tokens_prompt": d.tokens_prompt,
        "tokens_completion": d.tokens_completion,
        "cost_usd": d.cost_usd,
        "call_breakdown": d.call_breakdown or [],
        "latency_ms": d.latency_ms,
        "lead_score": d.lead_score,
        "lead_score_justification": d.lead_score_justification,
        "company_context": d.company_context,
        "core_problem": d.core_problem,
        "stated_vs_actual": d.stated_vs_actual or [],
        "spec_risk": d.spec_risk or [],
        "candidate_profiles": d.candidate_profiles or [],
        "search_booleans": d.search_booleans or {},
        "hiring_managers": d.hiring_managers or [],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
