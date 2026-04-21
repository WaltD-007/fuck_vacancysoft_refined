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
  POST   /api/leads/paste

Also houses the `_scrape_and_generate_dossier` background task that
`POST /api/queue` spawns as an in-process fallback when Redis is
unavailable.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from vacancysoft.api.ledger import (
    _AGGREGATOR_ADAPTERS,
    _CORE_MARKETS,
    _category_counts,
    _extract_employer_from_payload,
)
from vacancysoft.api.schemas import PasteLeadRequest, QueueRequest, StatsOut
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source


router = APIRouter(tags=["leads"])


# ── Dashboard cache ──────────────────────────────────────────────────────
# The /api/dashboard handler runs ~10 separate aggregate queries on every
# hit. That's expensive and the numbers don't change faster than ~30s in
# practice (the discovery worker runs on schedules, not on every request),
# so we cache the full response body in-process. Invalidated by mutation
# handlers (queue_campaign, send_to_campaign, remove_from_queue,
# mark_agency) so counts reflect user actions immediately.
#
# RAM cost: ~100 KB for the single "__all__" key.

_dashboard_cache: dict[str, tuple[float, dict]] = {}
_DASHBOARD_CACHE_TTL = 30  # seconds — matches _SOURCES_CACHE_TTL in ledger.py


def clear_dashboard_cache() -> None:
    """Drop the cached dashboard payload. Call from any handler that
    mutates data surfaced on the dashboard so the next request rebuilds."""
    _dashboard_cache.clear()


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
    import time as _time
    cache_key = "__all__"
    cached = _dashboard_cache.get(cache_key)
    if cached and (_time.time() - cached[0]) < _DASHBOARD_CACHE_TTL:
        return cached[1]

    from vacancysoft.db.models import ClassificationResult, SourceRun
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category
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
                ClassificationResult.sub_specialism,
                # EnrichedJob.id added 2026-04-21 so the Dashboard's
                # Live Feed rows can target the Dead job / Wrong
                # location admin buttons (same pair that lives on the
                # Sources page drawer via PR #36). Kept at the tail
                # of the SELECT so existing tuple index positions
                # stay stable.
                EnrichedJob.id,
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
            # sub_specialism now read directly from the DB column (2026-04-20);
            # previously recomputed via map_sub_specialism() against the
            # legacy YAML, which was stale vs the taxonomy code.
            sub = r[12] or "Other"

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
                # enriched_job_id — new 2026-04-21. Needed by the
                # Dashboard's Live Feed row-level admin buttons (Dead
                # job, Wrong location) to reference the DB row server-
                # side. Opaque UUID; safe to expose. Can be null in
                # theory for leads that lost their enriched_job
                # reference, but the join filters guarantee a non-
                # null value in practice.
                "id": r[13],
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

    payload = {
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
    _dashboard_cache[cache_key] = (_time.time(), payload)
    return payload


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

    # Queue length feeds the dashboard's campaigns_active / dossiers_active
    # tiles, so the stale cached copy must be dropped.
    clear_dashboard_cache()
    return {"message": "Queued", "id": item_id}


async def _scrape_and_generate_dossier(item_id: str, url: str | None, company: str | None, title: str | None):
    """Background task: scrape the job advert, store the description, then generate the dossier."""
    from vacancysoft.db.models import ReviewQueueItem
    from vacancysoft.intelligence.url_scrape import scrape_advert

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
                workday_cfg: dict | None = None
                raw = s.get(RawJob, enriched.raw_job_id)
                if raw:
                    src = s.get(Source, raw.source_id)
                    if src and src.adapter_name == "workday":
                        config = src.config_blob or {}
                        if config.get("tenant") and config.get("shard") and config.get("site_path"):
                            workday_cfg = {
                                "tenant": config["tenant"],
                                "shard": config["shard"],
                                "sitePath": config["site_path"],
                            }
                meta = await scrape_advert(url, workday=workday_cfg)
                description = (meta.get("description") or "").strip()
                if description and meta.get("status") in ("success", "empty"):
                    enriched.description_text = description
                    s.commit()
                elif meta.get("status") == "error":
                    import logging
                    logging.getLogger(__name__).warning("Scrape failed for %s: %s", url, meta.get("error"))

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
    """List all queued campaign leads, with the latest dossier inlined.

    A lead is only reported as 'ready' if an IntelligenceDossier actually exists
    for its enriched job. Otherwise we downgrade the reported status to
    'generating' so downstream consumers (e.g. the Campaign Builder) don't try
    to generate a campaign before the dossier is persisted.

    The `dossier` field on each row carries the most recent dossier payload
    for that lead (same shape as `GET /api/leads/{id}/dossier`) or `null` if
    none has been generated yet. Inlining this here lets the Leads page
    render the intelligence panel without an N+1 per-row fetch.
    """
    from vacancysoft.api.schemas import dossier_to_dict
    from vacancysoft.db.models import IntelligenceDossier, ReviewQueueItem
    with SessionLocal() as s:
        items = s.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.queue_type == "campaign")
            .order_by(ReviewQueueItem.created_at.desc())
        ).scalars().all()

        # Pull the latest IntelligenceDossier for every queued URL in one
        # shot. We match on URL rather than enriched_job_id because there
        # can be multiple EnrichedJob rows per URL and the queue item's
        # enriched_job_id may point at a different one than the dossier's.
        # Matching on URL is what the dossier / campaign endpoints
        # effectively do.
        urls = [u for u in ((i.evidence_blob or {}).get("url") for i in items) if u]
        dossiers_by_url: dict[str, IntelligenceDossier] = {}
        if urls:
            # Grab (url, dossier) pairs; newest-first so the first entry we
            # see for a URL is the one we keep.
            rows = s.execute(
                select(RawJob.discovered_url, IntelligenceDossier)
                .join(EnrichedJob, EnrichedJob.raw_job_id == RawJob.id)
                .join(IntelligenceDossier, IntelligenceDossier.enriched_job_id == EnrichedJob.id)
                .where(RawJob.discovered_url.in_(urls))
                .order_by(IntelligenceDossier.created_at.desc())
            ).all()
            for url, dossier in rows:
                if url not in dossiers_by_url:
                    dossiers_by_url[url] = dossier

        def _reported_status(item: ReviewQueueItem) -> str:
            if item.status != "ready":
                return item.status
            url = (item.evidence_blob or {}).get("url")
            if url and url in dossiers_by_url:
                return "ready"
            return "generating"

        out: list[dict] = []
        for item in items:
            evidence = item.evidence_blob or {}
            url = evidence.get("url")
            dossier = dossiers_by_url.get(url) if url else None
            out.append({
                "id": item.id,
                "status": _reported_status(item),
                "title": evidence.get("title", ""),
                "company": evidence.get("company", ""),
                "location": evidence.get("location"),
                "country": evidence.get("country"),
                "category": evidence.get("category"),
                "sub_specialism": evidence.get("sub_specialism"),
                "url": url,
                "score": evidence.get("score"),
                "board_url": evidence.get("board_url"),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "dossier": dossier_to_dict(dossier) if dossier else None,
            })
        return out


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

    clear_dashboard_cache()
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
    clear_dashboard_cache()
    return {"message": "Removed", "id": item_id}


# ── Paste a URL → full pipeline ──────────────────────────────────────────
#
# Takes a single advert URL from the operator, scrapes via the Playwright
# runner (which returns title/company/location/description), then runs the
# existing enrichment → classification → scoring → queue pipeline so the
# ARQ worker picks it up for dossier + campaign generation exactly like
# any other queued lead.
#
# Dedupe: if the URL already matches an EnrichedJob, reuse it instead of
# creating duplicate rows — the operator still gets a queue item + campaign
# regeneration either way.

_MANUAL_PASTE_SOURCE_KEY = "manual_paste"


def _is_linkedin_job_url(url: str) -> bool:
    """True when the URL is a LinkedIn job advert.

    LinkedIn job pages gate the advert body behind a login wall — the
    scraper either grabs the related-jobs sidebar (→ hallucinated dossier)
    or misses the body entirely. Rather than silently produce a poor
    dossier we reject these URLs and nudge the operator toward the
    'Apply on company website' ATS URL.

    Matches both the public `/jobs/view/...` layout and the `/comm/jobs/`
    email-share variant, on any LinkedIn subdomain (www, uk, de, etc.).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return False
    path = (parsed.path or "").lower()
    return path.startswith("/jobs/") or "/comm/jobs/" in path


# Recognises LinkedIn's public page-title wrapper, e.g.
#   "Abound hiring Head of Credit Risk in London, England, United Kingdom | LinkedIn"
# and captures the three substrings so we can back-fill company and location
# when the runner's structured extractor didn't manage to.
_LINKEDIN_TITLE_RE = re.compile(
    r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+?)\s+in\s+(?P<location>.+?)\s*\|\s*LinkedIn\s*$",
    re.IGNORECASE,
)

# Fallback suffix stripper for pages that have the " | LinkedIn" trailer
# but don't match the full "X hiring Y in Z" pattern.
_LINKEDIN_SUFFIX_RE = re.compile(r"\s*\|\s*LinkedIn\s*$", re.IGNORECASE)

# "<Role> at <Company>" — common ATS page-title pattern (Barclays, Greenhouse
# listings, etc.). Used as a fallback when the runner didn't extract company
# via JSON-LD / og:site_name.
_TITLE_AT_COMPANY_RE = re.compile(
    r"^(?P<title>.+?)\s+at\s+(?P<company>[^|\-–—]+?)\s*$",
    re.IGNORECASE,
)

# "<Role> - <Company>" / "<Role> — <Company>" — seen on generic careers pages.
# Conservative: the company side must have no digits (so "VP - 10+ years
# experience" doesn't parse as company="10+ years experience"), at most 5
# words, and at least 3 chars.
_TITLE_DASH_COMPANY_RE = re.compile(
    r"^(?P<title>.+?)\s+[-–—]\s+(?P<company>[A-Za-z][A-Za-z& ,.']{2,60})\s*$",
)


def _clean_scraped_title(raw_title: str) -> tuple[str, str, str]:
    """Strip platform wrappers from a scraped page title.

    Returns (clean_title, inferred_company, inferred_location). Each component
    is "" if it couldn't be parsed from the wrapper.

    Patterns, in order:
      1. LinkedIn's "<Company> hiring <Title> in <Location> | LinkedIn".
      2. Bare "| LinkedIn" suffix stripper (then continue to (3)/(4)).
      3. "<Role> at <Company>"   — Barclays careers, Greenhouse jobs, etc.
      4. "<Role> - <Company>"    — conservative dash split.
    """
    if not raw_title:
        return "", "", ""
    s = raw_title.strip()

    # LinkedIn full wrapper
    m = _LINKEDIN_TITLE_RE.match(s)
    if m:
        return (
            m.group("title").strip(),
            m.group("company").strip(),
            m.group("location").strip(),
        )

    # Drop any lingering " | LinkedIn" so downstream patterns match cleanly
    s = _LINKEDIN_SUFFIX_RE.sub("", s).strip()

    # "Role at Company"
    m = _TITLE_AT_COMPANY_RE.match(s)
    if m:
        return m.group("title").strip(), m.group("company").strip(), ""

    # "Role - Company"
    m = _TITLE_DASH_COMPANY_RE.match(s)
    if m:
        company_candidate = m.group("company").strip()
        # Guard: drop single-word candidates like "Permanent" or "Remote"
        # that are contract/location descriptors, not employers.
        word_count = len(company_candidate.split())
        if word_count <= 5 and word_count >= 1:
            return m.group("title").strip(), company_candidate, ""

    return s, "", ""


def _ensure_manual_paste_source(session) -> Source:
    """Find (or create) the shared 'Manual Paste' Source row that every
    paste-originated RawJob is filed under. One row, shared across all
    pastes — the employer on the card is resolved from enriched.team
    (extracted from the scrape) rather than this Source row's name."""
    src = session.execute(
        select(Source).where(Source.source_key == _MANUAL_PASTE_SOURCE_KEY)
    ).scalar_one_or_none()
    if src is not None:
        return src

    src = Source(
        source_key=_MANUAL_PASTE_SOURCE_KEY,
        employer_name="(Manual paste)",
        board_name=None,
        base_url="",
        hostname="manual-paste",
        source_type="manual",
        ats_family=None,
        adapter_name="manual_paste",
        active=True,
        seed_type="manual_paste",
        discovery_method="manual_paste",
        fingerprint="manual_paste",
        canonical_company_key="manual_paste",
        config_blob=None,
        capability_blob={},
    )
    session.add(src)
    session.flush()
    return src


@router.post("/api/leads/paste")
async def paste_lead(req: PasteLeadRequest, request: Request):
    """Paste a single advert URL and run the full pipeline.

    Flow (see the plan for the full architecture diagram):
      1. Dedupe on discovered_url — if an EnrichedJob exists, queue a
         campaign for it and return.
      2. Call the Playwright runner for {title, company, location,
         description, postedDate}.
      3. Ensure a shared "Manual Paste" Source row exists.
      4. Write SourceRun + ExtractionAttempt + RawJob rows.
      5. Run persist_enrichment_for_raw_job → persist_classification →
         persist_score (existing helpers).
      6. Create a ReviewQueueItem and enqueue process_lead on Redis
         (fall back to in-process task if Redis is unavailable).

    Returns:
      { "status": "queued" | "reused" | "already_queued",
        "item_id": "...", "enriched_id": "..." }

    Errors:
      400 — empty URL
      422 — scraper couldn't reach the URL, returned non-advert content,
            or the lead was filtered (wrong country / recruiter / title
            doesn't match any taxonomy keyword)
    """
    from vacancysoft.db.models import (
        ExtractionAttempt,
        ReviewQueueItem,
        SourceRun,
    )
    from vacancysoft.intelligence.url_scrape import scrape_advert
    from vacancysoft.pipelines.classification_persistence import (
        persist_classification_for_enriched_job,
    )
    from vacancysoft.pipelines.enrichment_persistence import persist_enrichment_for_raw_job
    from vacancysoft.pipelines.scoring_persistence import persist_score_for_enriched_job

    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    # Hard-reject LinkedIn job URLs. The advert body is consistently gated
    # behind a login wall so the scraper either grabs the related-jobs
    # sidebar (→ hallucinated dossier) or misses content entirely. Better
    # to fail loudly and nudge the operator toward the authoritative ATS
    # URL than silently produce a weak dossier.
    if _is_linkedin_job_url(url):
        raise HTTPException(
            status_code=422,
            detail=(
                "LinkedIn job URLs aren't supported — the advert body is "
                "gated behind a login wall and the scraper can't read it "
                "reliably. On the LinkedIn posting, click 'Apply on "
                "company website' (or similar) and paste that URL "
                "instead — Greenhouse, Workable, Workday, or the "
                "employer's own careers site will all scrape cleanly."
            ),
        )

    # ── 1. Dedupe ────────────────────────────────────────────────────────
    with SessionLocal() as s:
        existing_enriched_id = s.execute(
            select(EnrichedJob.id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .where(RawJob.discovered_url == url)
            .limit(1)
        ).scalar_one_or_none()

        if existing_enriched_id:
            # Already in the DB. If there's already a campaign queue item,
            # short-circuit — nothing more to do. Otherwise queue a fresh
            # one so the operator sees the lead on /leads.
            existing_item = s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.enriched_job_id == existing_enriched_id)
                .where(ReviewQueueItem.queue_type == "campaign")
                .limit(1)
            ).scalar_one_or_none()
            if existing_item is not None:
                return {
                    "status": "already_queued",
                    "item_id": existing_item.id,
                    "enriched_id": existing_enriched_id,
                }

            item = ReviewQueueItem(
                enriched_job_id=existing_enriched_id,
                queue_type="campaign",
                priority=50,
                reason_code="user_pasted_url",
                reason_summary=f"Paste: {url}",
                evidence_blob={"url": url, "source": "paste"},
                status="pending",
            )
            s.add(item)
            s.commit()
            s.refresh(item)
            item_id = item.id

        else:
            item_id = None

    if existing_enriched_id and item_id:
        # Enqueue + cache invalidate happens after the with-block committed.
        await _enqueue_process_lead(request, item_id, url, company="", title="")
        clear_dashboard_cache()
        return {"status": "reused", "item_id": item_id, "enriched_id": existing_enriched_id}

    # ── 2. Scrape via Playwright runner ──────────────────────────────────
    meta = await scrape_advert(url)
    status = meta.get("status")
    if status in ("error", "content_blocked"):
        raise HTTPException(
            status_code=422,
            detail=meta.get("error") or "Playwright runner could not read that URL",
        )

    raw_title = (meta.get("title") or "").strip()
    raw_company = (meta.get("company") or "").strip()
    raw_location = (meta.get("location") or "").strip()
    description = (meta.get("description") or "").strip()
    posted_at_raw = (meta.get("postedDate") or "").strip()

    # LinkedIn's document.title is "X hiring Y in Z | LinkedIn" — when the
    # runner's JSON-LD path missed, the scraped title + empty company/location
    # come through here. Parse the wrapper so we get a clean job title and
    # infer company + location when they're missing.
    cleaned_title, inferred_company, inferred_location = _clean_scraped_title(raw_title)
    title = cleaned_title or raw_title
    company = raw_company or inferred_company
    location = raw_location or inferred_location

    if not title:
        # No title = dossier prompt has nothing to select a category from
        # and downstream will almost certainly mis-classify. Surface to the
        # operator rather than silently create a useless lead.
        raise HTTPException(
            status_code=422,
            detail="Scrape succeeded but no job title was found on the page",
        )

    # ── 3-5. Persist + pipeline ──────────────────────────────────────────
    with SessionLocal() as s:
        paste_source = _ensure_manual_paste_source(s)

        source_run = SourceRun(
            source_id=paste_source.id,
            run_type="manual_paste",
            status="success",
            trigger="manual_paste",
            records_seen=1,
            raw_jobs_created=1,
            details_fetched=1,
        )
        s.add(source_run)
        s.flush()

        extraction_attempt = ExtractionAttempt(
            source_run_id=source_run.id,
            source_id=paste_source.id,
            stage="detail",
            method="browser",
            endpoint_url=url,
            status_code=200,
            success=True,
            completeness_score=0.8 if description else 0.4,
            confidence_score=0.8 if description else 0.4,
        )
        s.add(extraction_attempt)
        s.flush()

        fingerprint = hashlib.md5(url.encode("utf-8")).hexdigest()
        raw_job = RawJob(
            source_id=paste_source.id,
            source_run_id=source_run.id,
            extraction_attempt_id=extraction_attempt.id,
            external_job_id=fingerprint,
            canonical_url=url,
            discovered_url=url,
            apply_url=url,
            title_raw=title,
            location_raw=location,
            posted_at_raw=posted_at_raw or None,
            description_raw=description,
            listing_payload={
                "source": "manual_paste",
                "company": company,
                # _extract_employer_from_payload (enrichment_persistence.py)
                # looks for `company_name` / `employer_name` when deciding
                # whether to populate enriched.team. Alias here so the
                # company ends up on the card even though manual_paste
                # isn't on the aggregator list.
                "company_name": company,
                "playwright_meta": meta,
            },
            detail_payload=None,
            raw_text_blob=description,
            job_fingerprint=fingerprint,
            content_hash=hashlib.sha1(description.encode("utf-8")).hexdigest() if description else None,
            completeness_score=0.8 if description else 0.4,
            extraction_confidence=0.8 if description else 0.4,
            provenance_blob={"mode": "manual_paste", "url": url},
        )
        s.add(raw_job)
        s.flush()

        # Enrich, classify, score — existing helpers. Any of these returning
        # None means a filter rejected the lead; surface the reason to the
        # operator.
        enriched = persist_enrichment_for_raw_job(s, raw_job)
        if enriched is None:
            # Look up what filter tripped so the error message is useful.
            s.rollback()
            reason = _paste_filter_reason(url, title, location, company)
            raise HTTPException(status_code=422, detail=reason)

        classification = persist_classification_for_enriched_job(s, enriched)
        if classification is None:
            s.rollback()
            raise HTTPException(
                status_code=422,
                detail=f"Could not classify title '{title}' — no taxonomy match",
            )

        score_row = persist_score_for_enriched_job(s, enriched)

        # Backfill enriched.team from the resolved company. The enrichment
        # helper's _extract_employer_from_payload already reads
        # listing_payload["company_name"] — this is a belt-and-braces
        # guarantee so downstream (dossier prompt, HM SerpApi queries) has
        # a real employer name and not the "(Manual paste)" placeholder
        # that the Source row carries.
        if company and not (enriched.team or "").strip():
            enriched.team = company
            s.flush()


        from vacancysoft.exporters.legacy_mapping import (
            load_legacy_routing,
            map_category,
        )
        routing = load_legacy_routing()
        cat_label = map_category(classification.primary_taxonomy_key, routing)
        # Sub-specialism comes from the ClassificationResult row we just
        # persisted (DB column added 2026-04-20). Previously recomputed via
        # map_sub_specialism() against the legacy YAML.
        sub_label = classification.sub_specialism or "Other"
        score_ui: float | None = None
        if score_row and score_row.export_eligibility_score is not None:
            score_ui = round(float(score_row.export_eligibility_score) * 10, 1)

        display_company = enriched.team or company or "(unknown)"
        display_location = enriched.location_city or location or None
        display_country = enriched.location_country

        item = ReviewQueueItem(
            enriched_job_id=enriched.id,
            queue_type="campaign",
            priority=50,
            reason_code="user_pasted_url",
            reason_summary=f"Paste: {title} — {display_company}",
            evidence_blob={
                "url": url,
                "title": enriched.title or title,
                "company": display_company,
                "location": display_location,
                "country": display_country,
                "category": cat_label,
                "sub_specialism": sub_label,
                "score": score_ui,
                "source": "paste",
            },
            status="pending",
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        item_id = item.id
        enriched_id = enriched.id

    # ── 6. Enqueue ───────────────────────────────────────────────────────
    await _enqueue_process_lead(request, item_id, url, company=company, title=title)
    clear_dashboard_cache()
    return {"status": "queued", "item_id": item_id, "enriched_id": enriched_id}


def _paste_filter_reason(url: str, title: str, location: str, company: str) -> str:
    """Best-guess explanation of why persist_enrichment_for_raw_job rejected
    a paste. The helper marks the RawJob with detail_fetch_status of
    geo_filtered / agency_filtered / title_filtered, but we've rolled back
    the transaction by the time we get here, so replay the checks."""
    from vacancysoft.enrichers.location_normaliser import is_allowed_country, normalise_location
    from vacancysoft.enrichers.recruiter_filter import is_recruiter
    from vacancysoft.classifiers.title_rules import is_relevant_title

    loc = normalise_location(location) if location else {}
    if location and not is_allowed_country(loc.get("country")):
        return (
            f"Location '{location}' is outside the supported country list"
            " (UK, USA, Canada, France, Germany, Switzerland, Netherlands,"
            " Luxembourg, UAE, Saudi Arabia, Hong Kong, Singapore)"
        )
    if company and is_recruiter(company):
        return f"'{company}' is on the recruitment-agency exclusion list"
    if title and not is_relevant_title(title):
        return (
            f"Title '{title}' doesn't match any core-market taxonomy keyword"
            " (risk, quant, compliance, audit, cyber, legal, front office)"
        )
    return f"Lead was filtered during enrichment (url={url})"


async def _enqueue_process_lead(
    request: Request,
    item_id: str,
    url: str,
    *,
    company: str,
    title: str,
) -> None:
    """Push a process_lead job onto Redis, or run the in-process fallback if
    Redis is unavailable. Mirrors the behaviour of POST /api/queue so the
    paste path and the Add-Lead path converge on the same worker."""
    if getattr(request.app.state, "redis", None):
        await request.app.state.redis.enqueue_job(
            "process_lead", item_id, url, company, title,
        )
        return
    import asyncio
    asyncio.ensure_future(
        _scrape_and_generate_dossier(item_id, url, company, title)
    )


# ── Sources page admin actions ──────────────────────────────────────
#
# Operator actions on the Sources page's expanded card "Jobs" drawer.
# Each job row carries three buttons:
#
#   * Agy job         → POST /api/agency (in routes/campaigns.py) — the
#                       existing handler already blocklists the employer
#                       and cascades all their EnrichedJobs / dossiers /
#                       campaigns / queue items / scores / classifications.
#                       No new endpoint needed for this button.
#   * Dead job        → DELETE /api/leads/{enriched_job_id} (below).
#                       Hard-deletes the single job and marks the underlying
#                       RawJob.is_deleted_at_source=True so the enrichment
#                       pipeline's NOT EXISTS scan (pipelines/enrichment_
#                       persistence.py) skips it on the next run and the
#                       job doesn't come back on the next scrape.
#   * Wrong location  → POST /api/leads/{enriched_job_id}/flag-location
#                       (below). Non-destructive — inserts a row into
#                       location_review_queue (migration 0010) for a
#                       future /review UI to pick up.


@router.delete("/api/leads/{enriched_job_id}")
def delete_lead(enriched_job_id: str):
    """Hard-delete a single enriched job and suppress re-enrichment.

    Cascade delete order mirrors ``/api/agency`` (routes/campaigns.py)
    for consistency: dossiers → campaign_outputs → review_queue →
    scores → classifications → enriched_job. The RawJob is preserved
    (it's the scraper's record of what was on the ATS) but its
    ``is_deleted_at_source`` column is flipped to True so the
    enrichment pass in ``pipelines/enrichment_persistence.py`` skips
    it on the next run — otherwise the lead would re-appear on the
    next scrape tick.

    Deliberately does NOT auto-resolve any LocationReviewFlag rows
    pointing at this enriched_job — those are audit rows, not live
    state. See migration 0010's docstring for the rationale.

    Returns a small dict with the rowcounts for each stage so the
    frontend can optimistically confirm the job is gone.
    """
    from sqlalchemy import delete as sa_delete
    from vacancysoft.db.models import (
        CampaignOutput,
        ClassificationResult,
        IntelligenceDossier,
        ReviewQueueItem,
    )

    with SessionLocal() as s:
        ej = s.execute(
            select(EnrichedJob).where(EnrichedJob.id == enriched_job_id)
        ).scalar_one_or_none()
        if ej is None:
            raise HTTPException(status_code=404, detail="enriched job not found")

        raw_job_id = ej.raw_job_id

        dossier_ids = [
            d.id for d in s.execute(
                select(IntelligenceDossier).where(
                    IntelligenceDossier.enriched_job_id == enriched_job_id
                )
            ).scalars()
        ]
        deleted_campaigns = 0
        if dossier_ids:
            deleted_campaigns = s.execute(
                sa_delete(CampaignOutput).where(CampaignOutput.dossier_id.in_(dossier_ids))
            ).rowcount or 0
        deleted_dossiers = s.execute(
            sa_delete(IntelligenceDossier).where(
                IntelligenceDossier.enriched_job_id == enriched_job_id
            )
        ).rowcount or 0
        deleted_queue = s.execute(
            sa_delete(ReviewQueueItem).where(
                ReviewQueueItem.enriched_job_id == enriched_job_id
            )
        ).rowcount or 0
        deleted_scores = s.execute(
            sa_delete(ScoreResult).where(
                ScoreResult.enriched_job_id == enriched_job_id
            )
        ).rowcount or 0
        deleted_classifications = s.execute(
            sa_delete(ClassificationResult).where(
                ClassificationResult.enriched_job_id == enriched_job_id
            )
        ).rowcount or 0
        s.execute(
            sa_delete(EnrichedJob).where(EnrichedJob.id == enriched_job_id)
        )

        # Mark the RawJob so enrichment doesn't re-create the EnrichedJob
        # on the next pipeline pass. The column existed historically for
        # "the ATS removed this posting" but nothing was reading it —
        # this is the first live use (paired with the skip filter added
        # to pipelines/enrichment_persistence.py).
        raw = s.execute(
            select(RawJob).where(RawJob.id == raw_job_id)
        ).scalar_one_or_none()
        if raw is not None:
            raw.is_deleted_at_source = True

        s.commit()

    # Dead jobs vanish from every dashboard total and the per-card
    # count, so both caches need to drop.
    from vacancysoft.api.ledger import clear_ledger_caches
    clear_ledger_caches()
    clear_dashboard_cache()

    return {
        "message": "deleted",
        "enriched_job_id": enriched_job_id,
        "deleted_dossiers": deleted_dossiers,
        "deleted_campaigns": deleted_campaigns,
        "deleted_queue_items": deleted_queue,
        "deleted_scores": deleted_scores,
        "deleted_classifications": deleted_classifications,
    }


@router.post("/api/leads/{enriched_job_id}/flag-location")
def flag_location(enriched_job_id: str, payload: dict | None = None):
    """Flag an enriched job's location — auto-apply when possible.

    Two-mode endpoint. If the operator's note parses cleanly through
    ``normalise_location()`` (confidence ≥ 0.7, i.e. the operator
    typed something like "Buffalo, NY, USA" or "New York, USA"), the
    correction is **applied immediately**:
      1. Update ``enriched_jobs.location_city`` / ``location_country``
         on the target row AND any other enriched_jobs for the same
         underlying URL (Google Jobs and a few aggregators can seed
         multiple enriched rows for one advert; we want them all
         corrected in one click).
      2. Insert a ``location_review_queue`` row with ``resolved=True``
         and ``resolved_at=NOW()`` so the review log still captures
         the event for audit.
      3. Drop the /sources and /dashboard caches so counts reflect
         the new country.

    If the note is empty or un-parseable (operator just wanted to
    flag without correcting), we fall back to the original behaviour:
    insert an unresolved row into ``location_review_queue`` for a
    future /review UI to pick up.

    Body shape:
      { "note": "Buffalo, NY, USA", "flagged_by_user_id": "..." }

    Response shape:
      { "status": "applied" | "queued",
        "flag_id": "...",
        "enriched_job_id": "...",
        "city": "Buffalo",         # only when status=applied
        "country": "USA",          # only when status=applied
        "affected_enriched_ids": [...]  # only when status=applied
      }
    """
    from vacancysoft.db.models import LocationReviewFlag
    from vacancysoft.enrichers.location_normaliser import normalise_location

    payload = payload or {}
    note = str(payload.get("note") or "").strip()
    flagged_by = payload.get("flagged_by_user_id")
    if flagged_by is not None:
        flagged_by = str(flagged_by).strip() or None

    # Try to parse the note. Confidence threshold 0.7 matches the
    # scraper's own bar for treating a parse as trustworthy — below
    # that and we'd risk writing noise into enriched_jobs.
    parsed = normalise_location(note) if note else None
    apply = bool(
        parsed
        and parsed.get("city")
        and parsed.get("country")
        and (parsed.get("confidence") or 0) >= 0.7
    )

    with SessionLocal() as s:
        target = s.execute(
            select(EnrichedJob).where(EnrichedJob.id == enriched_job_id)
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="enriched job not found")

        affected_ids: list[str] = []
        if apply:
            # Resolve the URL so we can correct any sibling enriched_jobs
            # created from separate raw_jobs that share the URL. Google
            # Jobs and a handful of aggregators emit one RawJob per
            # search-refresh — without this, the operator would have
            # to fix the same advert two or three times.
            url = s.execute(
                select(RawJob.discovered_url)
                .where(RawJob.id == target.raw_job_id)
            ).scalar_one_or_none()

            siblings: list[EnrichedJob] = [target]
            if url:
                siblings = list(s.execute(
                    select(EnrichedJob)
                    .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
                    .where(RawJob.discovered_url == url)
                ).scalars())

            for ej in siblings:
                ej.location_city = parsed["city"]
                ej.location_country = parsed["country"]
                affected_ids.append(ej.id)

        flag = LocationReviewFlag(
            enriched_job_id=enriched_job_id,
            flagged_by_user_id=flagged_by,
            note=note,
            resolved=apply,
            resolved_at=datetime.utcnow() if apply else None,
        )
        s.add(flag)
        s.commit()
        s.refresh(flag)
        flag_id = flag.id

    if apply:
        # Country changes mean /sources card counts (country-scoped)
        # and /dashboard daily totals need to rebuild. Mutation-time
        # cache drop is cheap — next request re-materialises.
        from vacancysoft.api.ledger import clear_ledger_caches
        clear_ledger_caches()
        clear_dashboard_cache()
        return {
            "status": "applied",
            "flag_id": flag_id,
            "enriched_job_id": enriched_job_id,
            "city": parsed["city"],
            "country": parsed["country"],
            "affected_enriched_ids": affected_ids,
        }

    return {
        "status": "queued",
        "flag_id": flag_id,
        "enriched_job_id": enriched_job_id,
    }
