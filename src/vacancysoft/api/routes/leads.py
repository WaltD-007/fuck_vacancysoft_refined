"""Leads / stats / dashboard / queue endpoints.

Groups the endpoints that feed the Leads page, the Dashboard, and the
campaign-queue lifecycle:

  GET    /api/stats
  GET    /api/dashboard
  GET    /api/countries
  POST   /api/queue
  GET    /api/queue
  POST   /api/queue/{item_id}/send
  DELETE /api/queue/{item_id}

Also houses the `_scrape_and_generate_dossier` background task that
`POST /api/queue` spawns as an in-process fallback when Redis is
unavailable. Extracted verbatim from `api/server.py` during the
Week 4 split.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from vacancysoft.api.ledger import (
    _AGGREGATOR_ADAPTERS,
    _CORE_MARKETS,
    _category_counts,
    _extract_employer_from_payload,
)
from vacancysoft.api.schemas import QueueRequest, StatsOut
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source


router = APIRouter(tags=["leads"])


_PLAYWRIGHT_SCRAPER_URL = "https://playwright-runner.bluecliff-1ceb6690.uksouth.azurecontainerapps.io/scrape"


@router.get("/api/stats", response_model=StatsOut)
def get_stats(country: str | None = None):
    with SessionLocal() as s:
        total = s.execute(select(func.count()).select_from(Source)).scalar() or 0
        active = s.execute(select(func.count(func.distinct(Source.employer_name))).where(Source.active.is_(True)).where(Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS))).scalar() or 0
        # Restrict raw_jobs and enriched_jobs totals to active sources so removing a
        # company instantly drops its jobs from the dashboard / sidebar counters.
        jobs = s.execute(
            select(func.count()).select_from(RawJob)
            .join(Source, RawJob.source_id == Source.id)
            .where(Source.active.is_(True))
        ).scalar() or 0
        enriched = s.execute(
            select(func.count()).select_from(EnrichedJob)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(Source.active.is_(True))
        ).scalar() or 0
        cats = _category_counts(s, country=country)
        scored = sum(cats.values())
        adapter_counts = dict(s.execute(
            select(Source.adapter_name, func.count())
            .where(Source.active.is_(True))
            .group_by(Source.adapter_name)
        ).all())
    return StatsOut(
        total_sources=total, active_sources=active,
        total_jobs=jobs, total_enriched=enriched, total_scored=scored,
        adapters=adapter_counts, categories=cats,
    )


@router.get("/api/dashboard")
def get_dashboard():
    """Dashboard data: recent leads, category counts, source health, plus real
    top-of-page stats (no random / placeholder numbers).

    Returns:
      * total_scored           core-market classified lead count
      * total_jobs             all raw_jobs regardless of classification
      * active_sources         active direct (non-aggregator) sources
      * broken_sources         sources with any recorded error
      * categories             core-market breakdown
      * leads_today            core-market leads enriched in last 24 h
      * leads_yesterday        core-market leads enriched 24–48 h ago (for delta)
      * avg_score              mean export_eligibility_score × 10, core only
      * avg_score_prev_week    same metric for 7–14 days ago (for delta)
      * campaigns_active       rows in campaign_outputs
      * dossiers_active        rows in intelligence_dossiers
      * daily_leads            list of 30 ints, oldest first — daily core-market counts
      * recent_leads           list of the most recent core-market leads, each with a
                               real `score` (0–10) and `discovered` ISO timestamp
      * source_health          last 20 scrape runs
    """
    from vacancysoft.db.models import ClassificationResult, SourceRun
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category, map_sub_specialism
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as _sql_text

    routing = load_legacy_routing()
    now = datetime.now(timezone.utc)

    with SessionLocal() as s:
        cats = _category_counts(s)
        total_scored = sum(cats.values())
        # total_jobs is restricted to ACTIVE sources — deactivating a company
        # instantly drops its raw/enriched/classified rows from every dashboard total.
        total_jobs = s.execute(
            select(func.count()).select_from(RawJob)
            .join(Source, RawJob.source_id == Source.id)
            .where(Source.active.is_(True))
        ).scalar() or 0
        active = s.execute(
            select(func.count(func.distinct(Source.employer_name)))
            .where(Source.active.is_(True))
            .where(Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS))
        ).scalar() or 0
        broken = s.execute(
            select(func.count(func.distinct(SourceRun.source_id)))
            .join(Source, Source.id == SourceRun.source_id)
            .where(SourceRun.status == "error")
            .where(Source.active.is_(True))
        ).scalar() or 0

        # Today vs yesterday (core-market leads created) for delta-style UI widgets
        leads_today = s.execute(
            select(func.count()).select_from(EnrichedJob)
            .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
            .where(EnrichedJob.created_at >= now - timedelta(hours=24))
        ).scalar() or 0
        leads_yesterday = s.execute(
            select(func.count()).select_from(EnrichedJob)
            .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
            .where(EnrichedJob.created_at >= now - timedelta(hours=48))
            .where(EnrichedJob.created_at < now - timedelta(hours=24))
        ).scalar() or 0

        # Avg score across core markets (export_eligibility_score is 0–1; × 10 for 0–10 UI scale)
        avg_raw = s.execute(
            select(func.avg(ScoreResult.export_eligibility_score))
            .join(ClassificationResult, ClassificationResult.enriched_job_id == ScoreResult.enriched_job_id)
            .join(EnrichedJob, EnrichedJob.id == ScoreResult.enriched_job_id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
        ).scalar()
        avg_score = round(float(avg_raw) * 10, 1) if avg_raw is not None else 0.0
        avg_prev = s.execute(
            select(func.avg(ScoreResult.export_eligibility_score))
            .join(EnrichedJob, EnrichedJob.id == ScoreResult.enriched_job_id)
            .join(ClassificationResult, ClassificationResult.enriched_job_id == ScoreResult.enriched_job_id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
            .where(EnrichedJob.created_at >= now - timedelta(days=14))
            .where(EnrichedJob.created_at < now - timedelta(days=7))
        ).scalar()
        avg_score_prev_week = round(float(avg_prev) * 10, 1) if avg_prev is not None else None

        # Campaigns + dossiers rendered (tables may not exist in very fresh DBs)
        campaigns_active = 0
        dossiers_active = 0
        try:
            campaigns_active = s.execute(_sql_text("SELECT COUNT(*) FROM campaign_outputs")).scalar() or 0
        except Exception:
            pass
        try:
            dossiers_active = s.execute(_sql_text("SELECT COUNT(*) FROM intelligence_dossiers")).scalar() or 0
        except Exception:
            pass

        # Daily histogram — last 30 days, oldest first, core markets only, active sources only
        daily_rows = s.execute(_sql_text("""
            SELECT generate_series::date AS day,
                   COALESCE(c.n, 0) AS n
            FROM generate_series((NOW() - INTERVAL '29 days')::date, NOW()::date, '1 day') AS generate_series
            LEFT JOIN (
                SELECT ej.created_at::date AS d, COUNT(*) AS n
                FROM enriched_jobs ej
                JOIN classification_results cr ON cr.enriched_job_id = ej.id
                JOIN raw_jobs rj ON rj.id = ej.raw_job_id
                JOIN sources src ON src.id = rj.source_id
                WHERE cr.primary_taxonomy_key IN ('risk','quant','compliance','audit','cyber','legal','front_office')
                  AND src.active = true
                  AND ej.created_at >= NOW() - INTERVAL '30 days'
                GROUP BY ej.created_at::date
            ) c ON c.d = generate_series::date
            ORDER BY generate_series
        """)).all()
        daily_leads = [int(r[1]) for r in daily_rows]

        # Recent leads — last 7 days, core markets only, active sources only, WITH real per-lead score
        cutoff = now - timedelta(days=7)
        recent = s.execute(
            select(
                EnrichedJob.title,
                Source.employer_name,
                EnrichedJob.location_city,
                EnrichedJob.location_country,
                ClassificationResult.primary_taxonomy_key,
                RawJob.discovered_url,
                EnrichedJob.created_at,
                Source.adapter_name,
                RawJob.listing_payload,
                Source.base_url,
                ScoreResult.export_eligibility_score,
                ClassificationResult.employment_type,
            )
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
            .outerjoin(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
            .where(EnrichedJob.created_at >= cutoff)
            .order_by(EnrichedJob.created_at.desc())
            .limit(10000)
        ).all()

        leads = []
        for r in recent:
            title = r[0] or ""
            tax_key = r[4]
            adapter = r[7] or ""
            payload = r[8]
            score_raw = r[10]
            category = map_category(tax_key, routing)
            sub = map_sub_specialism(title, category, routing)

            # For aggregator jobs, extract the real employer from payload.
            # Uses the shared extractor (which knows Reed's employerName,
            # eFC's companyName, etc.) — falls back to the source's
            # employer_name (e.g. "Reed", "Adzuna") only if nothing real
            # could be parsed from the payload.
            company = r[1] or ""
            if adapter in _AGGREGATOR_ADAPTERS:
                extracted = _extract_employer_from_payload(payload)
                if extracted:
                    company = extracted

            leads.append({
                "title": title,
                "company": company,
                "location": r[2],
                "country": r[3],
                "category": category,
                "sub_specialism": sub,
                "url": r[5],
                "discovered": r[6].isoformat() if r[6] else None,
                "board_url": r[9] or "",
                "score": round(float(score_raw) * 10, 1) if score_raw is not None else None,
                "employment_type": r[11] or "Permanent",
            })

        # Source health: last 20 runs
        health = s.execute(
            select(Source.employer_name, Source.adapter_name, SourceRun.status, SourceRun.raw_jobs_created, SourceRun.duration_ms)
            .join(Source, SourceRun.source_id == Source.id)
            .order_by(SourceRun.id.desc())
            .limit(20)
        ).all()

        source_health = [
            {"company": h[0], "adapter": h[1], "status": h[2], "jobs": h[3] or 0, "duration_ms": h[4] or 0}
            for h in health
        ]

    return {
        "total_scored": total_scored,
        "total_jobs": total_jobs,
        "active_sources": active,
        "broken_sources": broken,
        "categories": cats,
        "recent_leads": leads,
        "source_health": source_health,
        "daily_leads": daily_leads,
        "leads_today": int(leads_today),
        "leads_yesterday": int(leads_yesterday),
        "avg_score": avg_score,
        "avg_score_prev_week": avg_score_prev_week,
        "campaigns_active": int(campaigns_active),
        "dossiers_active": int(dossiers_active),
    }


@router.get("/api/countries")
def list_countries():
    """Return all countries with core market leads from ACTIVE sources, sorted by count."""
    from vacancysoft.db.models import ClassificationResult
    with SessionLocal() as s:
        rows = s.execute(
            select(EnrichedJob.location_country, func.count())
            .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
            .where(Source.active.is_(True))
            .where(EnrichedJob.location_country.isnot(None))
            .where(EnrichedJob.location_country != "")
            .where(EnrichedJob.location_country != "N/A")
            .group_by(EnrichedJob.location_country)
            .order_by(func.count().desc())
        ).all()
    return [{"country": r[0], "count": r[1]} for r in rows]


# ── Queue Campaign ──


@router.post("/api/queue")
async def queue_campaign(req: QueueRequest, request: Request):
    """Add a lead to the campaign queue and trigger dossier + campaign generation via the worker."""
    from vacancysoft.db.models import ReviewQueueItem

    with SessionLocal() as s:
        # Find the enriched job by URL if possible
        enriched_id = None
        if req.url:
            row = s.execute(
                select(EnrichedJob.id)
                .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                .where(RawJob.discovered_url == req.url)
                .limit(1)
            ).scalar_one_or_none()
            enriched_id = row

        # Check if already queued
        if enriched_id:
            existing = s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.enriched_job_id == enriched_id)
                .where(ReviewQueueItem.queue_type == "campaign")
            ).scalar_one_or_none()
            if existing:
                return {"message": "Already queued", "id": existing.id}

        # Generate a job ref
        job_ref = f"lead-{(req.company or 'unknown').lower().replace(' ', '-')[:20]}-{hashlib.md5((req.url or req.title or '').encode()).hexdigest()[:10]}"

        item = ReviewQueueItem(
            enriched_job_id=enriched_id,
            queue_type="campaign",
            priority=int((req.score or 5) * 10),
            reason_code="user_queued",
            reason_summary=f"{req.title} — {req.company}",
            evidence_blob={
                "title": req.title,
                "company": req.company,
                "location": req.location,
                "country": req.country,
                "category": req.category,
                "sub_specialism": req.sub_specialism,
                "url": req.url,
                "score": req.score,
                "board_url": req.board_url,
                "job_ref": job_ref,
            },
            status="pending",
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        item_id = item.id

    # Enqueue scrape + dossier generation via Redis worker
    if getattr(request.app.state, "redis", None):
        await request.app.state.redis.enqueue_job("process_lead", item_id, req.url, req.company, req.title)
    else:
        # Fallback: run in-process if Redis is unavailable
        import asyncio
        asyncio.ensure_future(_scrape_and_generate_dossier(item_id, req.url, req.company, req.title))

    return {"message": "Queued", "id": item_id}


async def _scrape_and_generate_dossier(item_id: str, url: str | None, company: str | None, title: str | None):
    """Background task: scrape the job advert, store the description, then generate the dossier."""
    import httpx as _httpx
    from vacancysoft.db.models import ReviewQueueItem

    try:
        with SessionLocal() as s:
            item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
            if not item:
                return

            # Update status to generating
            item.status = "generating"
            s.commit()

            # Find the enriched job
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
                item.status = "pending"
                s.commit()
                return

            # Step 1: If no description, scrape it
            if not (enriched.description_text or "").strip() and url:
                try:
                    # Build scrape payload — add Workday config if needed
                    scrape_body: dict = {"url": url}
                    raw = s.get(RawJob, enriched.raw_job_id)
                    if raw:
                        src = s.get(Source, raw.source_id)
                        if src and src.adapter_name == "workday":
                            config = src.config_blob or {}
                            if config.get("tenant") and config.get("shard") and config.get("site_path"):
                                scrape_body["workday"] = {
                                    "tenant": config["tenant"],
                                    "shard": config["shard"],
                                    "sitePath": config["site_path"],
                                }

                    async with _httpx.AsyncClient(timeout=120) as client:
                        resp = await client.post(_PLAYWRIGHT_SCRAPER_URL, json=scrape_body)
                        if resp.status_code == 200:
                            data = resp.json()
                            description = (data.get("description") or "").strip()
                            if description and data.get("status") in ("success", "empty"):
                                enriched.description_text = description
                                s.commit()
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Scrape failed for %s: %s", url, exc)

            # Step 2: Generate dossier
            from vacancysoft.intelligence.dossier import generate_dossier
            dossier = await generate_dossier(enriched.id, s)

            # Step 3: Pre-generate campaign. Non-fatal on failure — lead still
            # becomes ready; Builder will regenerate on demand if cache misses.
            try:
                from vacancysoft.intelligence.campaign import generate_campaign
                await generate_campaign(dossier.id, s)
            except Exception as camp_exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Campaign pre-generation failed for %s: %s",
                    item_id, camp_exc,
                )

            # Step 4: Update status to ready
            item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
            if item:
                item.status = "ready"
                s.commit()

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Background dossier generation failed for %s: %s", item_id, exc)
        try:
            with SessionLocal() as s:
                item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
                if item:
                    item.status = "pending"
                    s.commit()
        except Exception:
            pass


@router.get("/api/queue")
def list_queue():
    """List all queued campaign leads.

    A lead is only reported as 'ready' if an IntelligenceDossier actually exists
    for its enriched job. Otherwise we downgrade the reported status to
    'generating' so downstream consumers (e.g. the Campaign Builder) don't try
    to generate a campaign before the dossier is persisted.
    """
    from vacancysoft.db.models import IntelligenceDossier, ReviewQueueItem
    with SessionLocal() as s:
        items = s.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.queue_type == "campaign")
            .order_by(ReviewQueueItem.created_at.desc())
        ).scalars().all()

        # Which URLs have a dossier? (We match on URL rather than
        # enriched_job_id because there can be multiple EnrichedJob rows per
        # URL and the queue item's enriched_job_id may point at a different
        # one than the dossier's. Matching on URL is what the dossier /
        # campaign endpoints effectively do.) Single query, then set membership.
        urls = [
            (i.evidence_blob or {}).get("url")
            for i in items
        ]
        urls = [u for u in urls if u]
        urls_with_dossier: set[str] = set()
        if urls:
            urls_with_dossier = set(
                s.execute(
                    select(RawJob.discovered_url)
                    .join(EnrichedJob, EnrichedJob.raw_job_id == RawJob.id)
                    .join(IntelligenceDossier, IntelligenceDossier.enriched_job_id == EnrichedJob.id)
                    .where(RawJob.discovered_url.in_(urls))
                ).scalars().all()
            )

        def _reported_status(item: ReviewQueueItem) -> str:
            if item.status != "ready":
                return item.status
            url = (item.evidence_blob or {}).get("url")
            if url and url in urls_with_dossier:
                return "ready"
            return "generating"

        return [
            {
                "id": item.id,
                "status": _reported_status(item),
                "title": (item.evidence_blob or {}).get("title", ""),
                "company": (item.evidence_blob or {}).get("company", ""),
                "location": (item.evidence_blob or {}).get("location"),
                "country": (item.evidence_blob or {}).get("country"),
                "category": (item.evidence_blob or {}).get("category"),
                "sub_specialism": (item.evidence_blob or {}).get("sub_specialism"),
                "url": (item.evidence_blob or {}).get("url"),
                "score": (item.evidence_blob or {}).get("score"),
                "board_url": (item.evidence_blob or {}).get("board_url"),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ]


@router.post("/api/queue/{item_id}/send")
async def send_to_campaign(item_id: str):
    """Mark a queued lead as 'generating' so the operator UI reflects the
    campaign-build kickoff. Dossier + campaign generation themselves are
    handled by the worker via the original /api/queue enqueue."""
    from vacancysoft.db.models import ReviewQueueItem

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Not found")
        item.status = "generating"
        s.commit()

    return {"message": "Status updated to generating", "id": item_id}


@router.delete("/api/queue/{item_id}")
def remove_from_queue(item_id: str):
    from vacancysoft.db.models import ReviewQueueItem
    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Not found")
        from sqlalchemy import delete
        s.execute(delete(ReviewQueueItem).where(ReviewQueueItem.id == item_id))
        s.commit()
    return {"message": "Removed", "id": item_id}
