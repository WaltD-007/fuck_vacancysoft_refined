"""Prospero API — lightweight FastAPI server wrapping the scraping engine."""

from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, text, bindparam

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source, RawJob, EnrichedJob, ScoreResult
from vacancysoft.api.source_detector import detect_and_validate, detect_platform
from vacancysoft.source_registry.config_seed_loader import PLATFORM_REGISTRY

app = FastAPI(title="Prospero API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

class SourceOut(BaseModel):
    id: int
    employer_name: str
    adapter_name: str
    base_url: str
    active: bool
    seed_type: str
    ats_family: str | None
    jobs: int = 0
    enriched: int = 0
    scored: int = 0
    categories: dict[str, int] = {}
    categories_by_country: dict[str, dict[str, int]] = {}
    sub_specialisms: dict[str, dict[str, int]] = {}  # {category_label: {sub_specialism: count}}
    aggregator_hits: dict[str, int] = {}  # {adapter_name: count} for aggregator-contributed rows
    employment_types: dict[str, int] = {}  # {Permanent|Contract: count}
    last_run_status: str | None = None
    last_run_error: str | None = None

    class Config:
        from_attributes = True


class DetectRequest(BaseModel):
    url: str


class DetectResponse(BaseModel):
    adapter: str
    slug: str | None
    url: str
    company_guess: str
    reachable: bool
    job_count: int | None
    error: str | None


class AddSourceRequest(BaseModel):
    url: str
    company: str


class AddSourceResponse(BaseModel):
    id: int
    employer_name: str
    adapter_name: str
    base_url: str
    message: str


class AddCompanyRequest(BaseModel):
    company: str
    countries: list[str] | None = None  # defaults to ["United Kingdom"]
    days_back: int = 30
    employer_exact: str | None = None  # set by /confirm when user picks a specific candidate from the list


class AddCompanyCandidate(BaseModel):
    """One employer name that matches the fuzzy query, with jobs_count within the
    date/country window. Returned by /search so the UI can disambiguate."""
    employer_name: str
    jobs_count: int
    sample_title: str | None = None
    sample_location: str | None = None
    already_in_db: bool = False  # True if this exact employer already has a direct Source row


class AddCompanyResponse(BaseModel):
    """Response for BOTH /search (preview) and /confirm (commit).

    status values:
      * "ready"    — search found jobs, user can now confirm (returned by /search only)
      * "no_jobs"  — nothing to add (returned by /search only)
      * "exists"   — a direct card already exists; no Coresignal call made
      * "ok"       — card created and scraped (returned by /confirm only)
    """
    status: str
    jobs_found: int
    company: str
    source_id: int | None = None
    message: str
    candidates: list[AddCompanyCandidate] = []  # populated on /search when status="ready"


class StatsOut(BaseModel):
    total_sources: int
    active_sources: int
    total_jobs: int
    total_enriched: int
    total_scored: int
    adapters: dict[str, int]
    categories: dict[str, int]


# ── Helpers ──

def _slugify(v: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in v).split())


ADAPTER_MAP = {
    "greenhouse": "greenhouse", "workday": "workday", "lever": "lever",
    "icims": "icims", "ashby": "ashby", "smartrecruiters": "smartrecruiters",
    "workable": "workable", "oracle_cloud": "oracle", "successfactors": "successfactors",
    "eightfold": "eightfold", "pinpoint": "pinpoint", "hibob": "hibob",
    "taleo": "taleo", "teamtailor": "teamtailor", "generic_site": "generic_browser",
}


# ── Core markets only ──
_CORE_MARKETS = ("risk", "quant", "compliance", "audit", "cyber", "legal", "front_office")


_CATEGORY_LABELS = {
    "risk": "Risk",
    "quant": "Quant",
    "compliance": "Compliance",
    "audit": "Audit",
    "cyber": "Cyber",
    "legal": "Legal",
    "front_office": "Front Office",
}


def _category_counts(s, source_id: int | None = None, country: str | None = None) -> dict[str, int]:
    """Count classified jobs per core market category, optionally filtered by source and/or country.

    Only counts jobs from ACTIVE sources — deactivating a source immediately drops
    its contributions from every dashboard / stats / sidebar total so the UI
    reflects current board membership.
    """
    from vacancysoft.db.models import ClassificationResult
    q = (
        select(ClassificationResult.primary_taxonomy_key, func.count())
        .join(EnrichedJob, ClassificationResult.enriched_job_id == EnrichedJob.id)
        .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
        .join(Source, RawJob.source_id == Source.id)
        .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
        .where(Source.active.is_(True))
        .group_by(ClassificationResult.primary_taxonomy_key)
    )
    if source_id is not None:
        q = q.where(RawJob.source_id == source_id)
    if country is not None:
        q = q.where(EnrichedJob.location_country == country)
    raw = dict(s.execute(q).all())
    return {_CATEGORY_LABELS.get(k, k): v for k, v in raw.items()}


def _core_market_total(s, source_id: int | None = None) -> int:
    return sum(_category_counts(s, source_id).values())


# ── Routes ──

@app.get("/api/stats", response_model=StatsOut)
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


_AGGREGATOR_ADAPTERS = {"adzuna", "reed", "efinancialcareers", "google_jobs", "coresignal"}


@app.get("/api/dashboard")
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
    from vacancysoft.db.models import ClassificationResult, ScoreResult, SourceRun
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

            # For aggregator jobs, extract the real employer from payload
            company = r[1] or ""
            if adapter in _AGGREGATOR_ADAPTERS and isinstance(payload, dict):
                co_obj = payload.get("company")
                if isinstance(co_obj, dict):
                    company = co_obj.get("display_name") or company
                if company == r[1]:
                    company = payload.get("employer_name") or payload.get("companyName") or payload.get("company_name") or company

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


@app.get("/api/countries")
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


_sources_cache: dict[str, tuple[float, list]] = {}      # cached SourceOut lists, by country
_ledger_cache: dict[str, tuple[float, list]] = {}       # cached _LedgerCard lists, by country
_SOURCES_CACHE_TTL = 30  # seconds


def _extract_employer_from_payload(payload) -> str | None:
    """Best-effort extraction of the hiring company's display name from a raw
    aggregator listing payload (Adzuna/Reed/eFinancialCareers/Google Jobs/Coresignal).
    Returns None for anything we can't resolve."""
    if not isinstance(payload, dict):
        return None
    co = payload.get("company")
    if isinstance(co, dict):
        val = co.get("display_name")
        if val and str(val).strip():
            return str(val).strip()
    for key in ("employer_name", "companyName", "company_name"):
        val = payload.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def _build_source_card_ledger(session, country: str | None = None) -> list[dict]:
    """Build a canonical per-employer ledger of deduped core-market leads.

    Every lead from an active source that classifies into one of the seven core
    markets is placed on exactly one card, keyed by normalised employer name.
    Direct-source leads take precedence over aggregator-matched leads when
    (employer, title, category) collide. Aggregator leads with no extractable
    employer in their payload are dropped (no card to put them on).

    Returns: list of card dicts, each with keys:
        employer_display, employer_norm, card_id, adapter_name, base_url,
        active, seed_type, ats_family, direct_source_ids (list[int]),
        lead_ids (list[str] — enriched_job_ids, deduped),
        categories (dict[category_label, int]),
        categories_by_country (dict[country, dict[category_label, int]]),
        sub_specialisms (dict[category_label, dict[sub_label, int]]),
        aggregator_hits (dict[str, int]),
        raw_jobs_count (int), last_run_status, last_run_error.

    The same ledger is used by both the card listing endpoint (/api/sources)
    and the card-detail endpoint (/api/sources/{id}/jobs), so card counts and
    detail-view counts are always equal.
    """
    from vacancysoft.db.models import ClassificationResult, SourceRun
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_sub_specialism
    routing = load_legacy_routing()

    # ---- 1) Fetch every core-market lead from active sources ----
    q = (
        select(
            RawJob.id, RawJob.source_id, RawJob.listing_payload, RawJob.discovered_url,
            EnrichedJob.id, EnrichedJob.title, EnrichedJob.location_country,
            ClassificationResult.primary_taxonomy_key,
            ScoreResult.export_eligibility_score,
            Source.id, Source.employer_name, Source.adapter_name, Source.base_url,
            Source.active, Source.seed_type, Source.ats_family,
            ClassificationResult.employment_type,
        )
        .select_from(ClassificationResult)
        .join(EnrichedJob, ClassificationResult.enriched_job_id == EnrichedJob.id)
        .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
        .join(Source, RawJob.source_id == Source.id)
        .outerjoin(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id)
        .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
        .where(Source.active.is_(True))
    )
    if country:
        q = q.where(EnrichedJob.location_country == country)
    rows = session.execute(q).all()

    # ---- 2) Dedup by (employer_norm, title_key, category_key); direct wins ties ----
    dedup: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        (_raw_id, _raw_source_id, payload, _url,
         enriched_id, title, loc_country,
         cat_key, _score,
         src_id, src_employer_name, src_adapter, _src_base_url,
         _src_active, _src_seed, _src_ats, employment_type) = r

        is_aggregator = src_adapter in _AGGREGATOR_ADAPTERS
        if is_aggregator:
            employer_display = _extract_employer_from_payload(payload)
            if not employer_display:
                continue  # orphan aggregator lead — no card home
        else:
            employer_display = src_employer_name or ""
            if not employer_display.strip():
                continue
        employer_norm = employer_display.lower().strip()
        title_key = (title or "").lower().strip()[:60]
        dedup_key = (employer_norm, title_key, cat_key)

        record = {
            "enriched_id": enriched_id,
            "title": title or "",
            "cat_key": cat_key,
            "country": loc_country,
            "src_id": src_id,
            "adapter": src_adapter,
            "is_aggregator": is_aggregator,
            "employer_display": employer_display,
            "employer_norm": employer_norm,
            "employment_type": employment_type or "Permanent",
        }
        existing = dedup.get(dedup_key)
        if existing is None:
            dedup[dedup_key] = record
        elif existing["is_aggregator"] and not is_aggregator:
            dedup[dedup_key] = record  # promote to direct
        # else: keep existing (first-seen wins within same tier)

    # ---- 3) Aggregate deduped leads into per-employer cards ----
    cards: dict[str, dict] = {}
    for lead in dedup.values():
        card = cards.setdefault(lead["employer_norm"], {
            "employer_display": lead["employer_display"],
            "employer_norm": lead["employer_norm"],
            "card_id": 0,
            "adapter_name": "",
            "base_url": "",
            "active": True,
            "seed_type": None,
            "ats_family": None,
            "direct_source_ids": [],
            "lead_ids": [],
            "categories": {},
            "categories_by_country": {},
            "sub_specialisms": {},
            "aggregator_hits": {},
            "employment_types": {},
            "raw_jobs_count": 0,
            "last_run_status": None,
            "last_run_error": None,
        })
        card["lead_ids"].append(lead["enriched_id"])
        cat_label = _CATEGORY_LABELS.get(lead["cat_key"], lead["cat_key"])
        card["categories"][cat_label] = card["categories"].get(cat_label, 0) + 1
        country_key = lead["country"] or "N/A"
        card["categories_by_country"].setdefault(country_key, {})
        card["categories_by_country"][country_key][cat_label] = (
            card["categories_by_country"][country_key].get(cat_label, 0) + 1
        )
        # Sub-specialism bucket per (category, sub) — resolved from the lead's title + category
        # via the same rules that drive export-time sub-specialism labelling.
        sub_label = map_sub_specialism(lead["title"], cat_label, routing) or "Other"
        card["sub_specialisms"].setdefault(cat_label, {})
        card["sub_specialisms"][cat_label][sub_label] = (
            card["sub_specialisms"][cat_label].get(sub_label, 0) + 1
        )
        if lead["is_aggregator"]:
            card["aggregator_hits"][lead["adapter"]] = card["aggregator_hits"].get(lead["adapter"], 0) + 1
        et = lead.get("employment_type") or "Permanent"
        card["employment_types"][et] = card["employment_types"].get(et, 0) + 1

    # ---- 4) Resolve card metadata (card_id, run status, raw_jobs count) ----
    direct_sources = list(session.execute(
        select(Source).where(
            Source.active.is_(True),
            Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
        )
    ).scalars())
    direct_by_emp: dict[str, list] = {}
    for src in direct_sources:
        key = (src.employer_name or "").lower().strip()
        direct_by_emp.setdefault(key, []).append(src)

    raw_counts: dict[int, int] = {}
    if direct_sources:
        raw_counts = dict(session.execute(
            select(RawJob.source_id, func.count())
            .where(RawJob.source_id.in_([s.id for s in direct_sources]))
            .group_by(RawJob.source_id)
        ).all())

    last_runs: dict[int, tuple] = {}
    for src in direct_sources:
        lr = session.execute(
            select(SourceRun.status, SourceRun.diagnostics_blob)
            .where(SourceRun.source_id == src.id)
            .order_by(SourceRun.created_at.desc())
            .limit(1)
        ).first()
        if lr:
            last_runs[src.id] = lr

    virtual_counter = 0
    for card in cards.values():
        matches = direct_by_emp.get(card["employer_norm"], [])
        if matches:
            primary = matches[0]
            card["card_id"] = primary.id
            card["adapter_name"] = primary.adapter_name
            card["base_url"] = primary.base_url or ""
            card["active"] = primary.active
            card["seed_type"] = primary.seed_type
            card["ats_family"] = primary.ats_family
            card["direct_source_ids"] = [m.id for m in matches]
            card["raw_jobs_count"] = sum(raw_counts.get(m.id, 0) for m in matches)
            # Surface worst run status across all direct sources backing this card
            worst = None
            err = None
            for m in matches:
                lr = last_runs.get(m.id)
                if not lr:
                    continue
                status, diag = lr
                if status in ("error", "FAIL"):
                    worst = status
                    if isinstance(diag, dict):
                        err = diag.get("error") or "Unknown error"
                    break
                if worst is None:
                    worst = status
            card["last_run_status"] = worst
            card["last_run_error"] = err
        else:
            virtual_counter += 1
            card["card_id"] = -virtual_counter
            card["adapter_name"] = "aggregator"
            card["seed_type"] = "aggregator"

    return sorted(cards.values(), key=lambda c: len(c["lead_ids"]), reverse=True)


def _get_cached_ledger(country: str | None = None) -> list[dict]:
    """Ledger cache shared across /api/sources and /api/sources/{id}/jobs."""
    import time as _t
    key = country or "__all__"
    cached = _ledger_cache.get(key)
    if cached and (_t.time() - cached[0]) < _SOURCES_CACHE_TTL:
        return cached[1]
    with SessionLocal() as s:
        ledger = _build_source_card_ledger(s, country=country)
    _ledger_cache[key] = (_t.time(), ledger)
    return ledger


@app.get("/api/sources", response_model=list[SourceOut])
def list_sources(country: str | None = None):
    import time as _time
    cache_key = country or "__all__"
    cached = _sources_cache.get(cache_key)
    if cached and (_time.time() - cached[0]) < _SOURCES_CACHE_TTL:
        return cached[1]

    ledger = _get_cached_ledger(country=country)
    result = [
        SourceOut(
            id=card["card_id"],
            employer_name=card["employer_display"],
            adapter_name=card["adapter_name"] or "",
            base_url=card["base_url"] or "",
            active=card["active"],
            seed_type=card["seed_type"] or "aggregator",
            ats_family=card["ats_family"],
            jobs=card["raw_jobs_count"],
            enriched=0,
            scored=len(card["lead_ids"]),
            categories=card["categories"],
            categories_by_country=card["categories_by_country"],
            sub_specialisms=card.get("sub_specialisms", {}),
            aggregator_hits=card["aggregator_hits"],
            employment_types=card.get("employment_types", {}),
            last_run_status=card["last_run_status"],
            last_run_error=card["last_run_error"],
        )
        for card in ledger
    ]
    _sources_cache[cache_key] = (_time.time(), result)
    return result



class ScoredJobOut(BaseModel):
    title: str
    company: str
    location: str | None
    country: str | None
    category: str | None
    sub_specialism: str | None
    score: float | None
    url: str | None

    class Config:
        from_attributes = True


@app.get("/api/sources/{source_id}/jobs", response_model=list[ScoredJobOut])
def get_source_jobs(
    source_id: int,
    category: str | None = None,
    company: str | None = None,
    country: str | None = None,
):
    """Return the exact deduped lead set shown on one card.

    Pulls from the same ledger as ``/api/sources`` so the per-card count and
    the jobs returned here are always the same set. Accepts:
      * positive source_id — resolves to the direct Source, then looks up the
        ledger card by normalised employer_name (picks up sibling direct
        sources and matched aggregator leads for the same employer).
      * source_id == 0 with ?company= — virtual/aggregator-only card lookup.
      * negative source_id — treated as a virtual card; ?company= must be set.
    """
    from vacancysoft.db.models import ClassificationResult
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category, map_sub_specialism

    routing = load_legacy_routing()
    ledger = _get_cached_ledger(country=country)

    target = None
    if source_id > 0:
        # Direct card — try matching by card_id or any of its sibling direct source ids first
        for card in ledger:
            if card["card_id"] == source_id or source_id in card.get("direct_source_ids", []):
                target = card
                break
        # Fallback: resolve employer via the Source row
        if target is None:
            with SessionLocal() as s:
                src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
            if src:
                norm = (src.employer_name or "").lower().strip()
                for card in ledger:
                    if card["employer_norm"] == norm:
                        target = card
                        break
    elif source_id == 0 or source_id < 0:
        # Virtual / aggregator-only card — company param required
        if not company or not company.strip():
            raise HTTPException(status_code=400, detail="company query param required for virtual card")
        norm = company.lower().strip()
        for card in ledger:
            if card["employer_norm"] == norm:
                target = card
                break

    if target is None or not target["lead_ids"]:
        # No matching card or empty card — return empty list rather than 404 so the
        # UI can still render a gracefully-empty detail panel.
        return []

    # Optional category filter narrows the detail list to one taxonomy bucket.
    cat_key_filter: str | None = None
    if category:
        label_to_key = {v: k for k, v in _CATEGORY_LABELS.items()}
        cat_key_filter = label_to_key.get(category, category.lower().replace(" ", "_"))

    with SessionLocal() as s:
        q = (
            select(
                EnrichedJob.id,
                EnrichedJob.title,
                EnrichedJob.location_city,
                EnrichedJob.location_country,
                ClassificationResult.primary_taxonomy_key,
                ScoreResult.export_eligibility_score,
                RawJob.discovered_url,
            )
            .select_from(EnrichedJob)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .outerjoin(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
            .outerjoin(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id)
            .where(EnrichedJob.id.in_(target["lead_ids"]))
        )
        if cat_key_filter:
            q = q.where(ClassificationResult.primary_taxonomy_key == cat_key_filter)
        rows = s.execute(q.order_by(ScoreResult.export_eligibility_score.desc().nullslast())).all()

    result: list[ScoredJobOut] = []
    for r in rows:
        title = r[1] or ""
        cat_label = map_category(r[4], routing)
        sub_spec = map_sub_specialism(title, cat_label, routing)
        result.append(ScoredJobOut(
            title=title,
            company=target["employer_display"],
            location=r[2],
            country=r[3],
            category=cat_label,
            sub_specialism=sub_spec,
            score=round(r[5], 1) if r[5] else None,
            url=r[6],
        ))
    return result


@app.post("/api/sources/detect", response_model=DetectResponse)
async def detect_source(req: DetectRequest):
    result = await detect_and_validate(req.url, timeout=15)
    return DetectResponse(**result)


def _addcompany_slugify(s: str) -> str:
    return "_".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def _addcompany_source_key(company: str) -> str:
    import hashlib as _hash
    url_hash = _hash.md5(f"coresignal:{company}".encode()).hexdigest()[:8]
    return f"coresignal_{_addcompany_slugify(company)}_{url_hash}"


async def _addcompany_count_jobs(
    *, company: str, countries: list[str], since, api_key: str,
) -> int:
    """Run the count-only Coresignal taxonomy union search. Returns total unique IDs."""
    import httpx as _httpx
    from vacancysoft.adapters.coresignal import (
        load_taxonomy_title_phrases, _build_taxonomy_query, SEARCH_ENDPOINT,
    )
    phrases = load_taxonomy_title_phrases()
    if not phrases:
        raise HTTPException(status_code=500, detail="Taxonomy phrase list is empty — check configs/legacy_routing.yaml")
    total_ids: set[int] = set()
    async with _httpx.AsyncClient(
        timeout=30,
        headers={"apikey": api_key, "accept": "application/json", "Content-Type": "application/json"},
    ) as client:
        for country in countries:
            query = _build_taxonomy_query(
                company=company, country=country, since=since, title_phrases=phrases,
            )
            try:
                resp = await client.post(SEARCH_ENDPOINT, json=query)
            except _httpx.TimeoutException:
                raise HTTPException(status_code=504, detail="Coresignal search timed out")
            if resp.status_code == 402:
                raise HTTPException(status_code=402, detail="Coresignal: out of credits")
            if resp.status_code == 401:
                raise HTTPException(status_code=500, detail="Coresignal API key invalid")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Coresignal HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if isinstance(data, list):
                for x in data:
                    if isinstance(x, (int, str)) and str(x).isdigit():
                        total_ids.add(int(x))
    return len(total_ids)


async def _addcompany_list_candidates(
    *, company: str, countries: list[str], since, api_key: str,
) -> tuple[int, list[dict]]:
    """Run Coresignal /search/es_dsl/preview (same credit cost as /search, no /collect)
    and group matching jobs by employer name. Returns (total_rows_seen, [candidates]).

    Each candidate: {'employer_name','jobs_count','sample_title','sample_location'}.
    Preview caps at 20 rows per call so we run per-country and per-category to harvest
    a wider company spread without burning collect credits.
    """
    import httpx as _httpx
    from collections import Counter
    from vacancysoft.adapters.coresignal import (
        load_taxonomy_by_category, _build_taxonomy_query, PREVIEW_ENDPOINT,
    )
    by_cat = load_taxonomy_by_category()
    if not by_cat:
        raise HTTPException(status_code=500, detail="Taxonomy is empty — check configs/legacy_routing.yaml")

    employer_counts: Counter[str] = Counter()
    samples: dict[str, tuple[str, str]] = {}  # {employer_lower: (title, location)}
    total_rows = 0
    seen_ids: set[int] = set()

    async with _httpx.AsyncClient(
        timeout=30,
        headers={"apikey": api_key, "accept": "application/json", "Content-Type": "application/json"},
    ) as client:
        for country in countries:
            for cat_name, cat_terms in by_cat.items():
                query = _build_taxonomy_query(
                    company=company, country=country, since=since, title_phrases=cat_terms,
                )
                try:
                    resp = await client.post(PREVIEW_ENDPOINT, json=query)
                except _httpx.TimeoutException:
                    raise HTTPException(status_code=504, detail="Coresignal search timed out")
                if resp.status_code == 402:
                    raise HTTPException(status_code=402, detail="Coresignal: out of credits")
                if resp.status_code == 401:
                    raise HTTPException(status_code=500, detail="Coresignal API key invalid")
                if resp.status_code != 200:
                    # Swallow individual category failures — return what we have
                    continue
                rows = resp.json()
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    jid = row.get("id")
                    if jid is not None and jid in seen_ids:
                        continue
                    if jid is not None:
                        seen_ids.add(jid)
                    co = (row.get("company_name") or "").strip()
                    if not co:
                        continue
                    employer_counts[co] += 1
                    key = co.lower()
                    if key not in samples:
                        samples[key] = (
                            (row.get("title") or "").strip(),
                            (row.get("location") or "").strip(),
                        )
                    total_rows += 1

    candidates: list[dict] = []
    for employer, n in employer_counts.most_common():
        title, loc = samples.get(employer.lower(), ("", ""))
        candidates.append({
            "employer_name": employer,
            "jobs_count": n,
            "sample_title": title or None,
            "sample_location": loc or None,
        })
    # total unique jobs seen (capped by preview's 20/query but deduped across calls)
    return len(seen_ids), candidates


@app.post("/api/sources/add-company/search", response_model=AddCompanyResponse)
async def add_company_search(req: AddCompanyRequest):
    """Phase 1: preview-only. Runs the Coresignal candidate search + existence check.
    Never writes to the DB. Returns one of:
      * status="exists"  — a direct card for the typed company already exists
      * status="no_jobs" — Coresignal returned 0 results for the full taxonomy
      * status="ready"   — one or more employer names match; UI lists candidates for user to pick

    The `candidates` list lets the UI disambiguate fuzzy matches (e.g. "Meta" vs
    "Meta Financial" vs "Metacomp"). Each candidate shows exact employer name,
    the number of jobs attributable to that employer, a sample job title, and
    whether that exact employer is already in the DB.
    """
    import os
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    api_key = (os.getenv("CORESIGNAL_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="CORESIGNAL_API_KEY not configured on server")
    company = (req.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="company is required")

    # Step 0: existence check on the typed string (direct Source rows only).
    with SessionLocal() as s:
        existing_direct = s.execute(
            select(Source).where(
                func.lower(Source.employer_name) == company.lower(),
                Source.active.is_(True),
                Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
            )
        ).scalars().first()
        if existing_direct:
            return AddCompanyResponse(
                status="exists", jobs_found=0, company=company,
                source_id=existing_direct.id,
                message=f"A direct card for {existing_direct.employer_name} already exists — no Coresignal call made.",
            )

    # Step 1: preview search — returns candidates grouped by employer name.
    countries = req.countries or ["United Kingdom"]
    since = _dt.now(_tz.utc) - _td(days=max(req.days_back, 1))
    total_jobs, raw_candidates = await _addcompany_list_candidates(
        company=company, countries=countries, since=since, api_key=api_key,
    )
    if total_jobs == 0 or not raw_candidates:
        return AddCompanyResponse(
            status="no_jobs", jobs_found=0, company=company, source_id=None,
            message=f"No jobs found for {company} in the last {req.days_back} days across the taxonomy.",
        )

    # Annotate each candidate with `already_in_db` so the UI can disable/hide it.
    candidate_names = [c["employer_name"] for c in raw_candidates]
    with SessionLocal() as s:
        existing_rows = s.execute(
            select(Source.employer_name).where(
                func.lower(Source.employer_name).in_([n.lower() for n in candidate_names]),
                Source.active.is_(True),
                Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
            )
        ).all()
    existing_lower = {r[0].lower() for r in existing_rows}

    candidates = [
        AddCompanyCandidate(
            employer_name=c["employer_name"],
            jobs_count=c["jobs_count"],
            sample_title=c.get("sample_title"),
            sample_location=c.get("sample_location"),
            already_in_db=c["employer_name"].lower() in existing_lower,
        )
        for c in raw_candidates
    ]

    return AddCompanyResponse(
        status="ready",
        jobs_found=total_jobs,
        company=company,
        source_id=None,
        message=f"Found {total_jobs} jobs across {len(candidates)} employer(s). Pick which one to add.",
        candidates=candidates,
    )


@app.post("/api/sources/add-company/confirm", response_model=AddCompanyResponse)
async def add_company_confirm(req: AddCompanyRequest):
    """Phase 2: user has confirmed — create the Source row and run the capped scrape.
    Re-runs the existence check (idempotent protection against double-click).

    If `employer_exact` is provided it takes priority over `company` — the card and
    Coresignal `company_filter` will be pinned to the user's explicit pick.
    """
    import os
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    api_key = (os.getenv("CORESIGNAL_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="CORESIGNAL_API_KEY not configured on server")
    # Prefer employer_exact (user's explicit pick) over the fuzzy typed string
    company = (req.employer_exact or req.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="company is required")

    countries = req.countries or ["United Kingdom"]
    since = _dt.now(_tz.utc) - _td(days=max(req.days_back, 1))
    source_key = _addcompany_source_key(company)

    # Re-check existence (defence against a double-click between search and confirm)
    with SessionLocal() as s:
        existing_direct = s.execute(
            select(Source).where(
                func.lower(Source.employer_name) == company.lower(),
                Source.active.is_(True),
                Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
            )
        ).scalars().first()
        if existing_direct:
            return AddCompanyResponse(
                status="exists", jobs_found=0, company=company,
                source_id=existing_direct.id,
                message=f"A direct card for {existing_direct.employer_name} already exists.",
            )
        # Also guard against the same source_key being re-created concurrently
        same_key = s.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()
        if same_key:
            return AddCompanyResponse(
                status="exists", jobs_found=0, company=company,
                source_id=same_key.id,
                message=f"Coresignal source already exists for {company}.",
            )

        new_src = Source(
            source_key=source_key,
            employer_name=company,
            board_name="Coresignal (company)",
            base_url="https://api.coresignal.com/cdapi/v2/job_multi_source",
            hostname="api.coresignal.com",
            source_type="ats_api",
            ats_family="coresignal",
            adapter_name="coresignal",
            active=True,
            seed_type="add_company_ui",
            discovery_method="add_company",
            fingerprint=f"coresignal|{_addcompany_slugify(company)}",
            canonical_company_key=_addcompany_slugify(company),
            config_blob={
                "job_board_url": "https://api.coresignal.com/cdapi/v2/job_multi_source",
                "company_filter": company,
                "use_full_taxonomy": True,
                "countries": countries,
                # Credit-conservative default for add-company sources. Raise or remove
                # once Coresignal credit budget is lifted for production.
                "max_per_category": 5,
                "max_per_term": 500,
                "request_delay": 0.1,
            },
            capability_blob={},
        )
        s.add(new_src)
        s.commit()
        s.refresh(new_src)
        new_id = new_src.id

    # Run the scrape inline so the card is ready when the UI refreshes
    scrape_error: str | None = None
    try:
        from vacancysoft.worker.tasks import scrape_source as _scrape_task
        await _scrape_task({}, new_id)
    except Exception as e:
        scrape_error = f"{type(e).__name__}: {e}"

    _sources_cache.clear()
    _ledger_cache.clear()

    # Return the final scored count for the new card
    with SessionLocal() as s:
        scored_in_core = s.execute(text(
            """
            SELECT COUNT(*) FROM classification_results cr
            JOIN enriched_jobs ej ON ej.id = cr.enriched_job_id
            JOIN raw_jobs rj ON rj.id = ej.raw_job_id
            WHERE rj.source_id = :sid
              AND cr.primary_taxonomy_key IN :core
            """
        ).bindparams(bindparam("core", expanding=True)), {"sid": new_id, "core": list(_CORE_MARKETS)}).scalar() or 0

    if scrape_error:
        return AddCompanyResponse(
            status="ok", jobs_found=scored_in_core, company=company, source_id=new_id,
            message=f"Card created for {company} (id={new_id}) but scrape failed: {scrape_error}",
        )
    return AddCompanyResponse(
        status="ok", jobs_found=scored_in_core, company=company, source_id=new_id,
        message=f"Card created for {company} with {scored_in_core} scored jobs.",
    )


@app.post("/api/sources", response_model=AddSourceResponse)
async def add_source(req: AddSourceRequest):
    result = await detect_and_validate(req.url, timeout=15)
    adapter = result["adapter"]
    slug = result["slug"]

    platform_key = ADAPTER_MAP.get(adapter, "generic_browser")
    meta = PLATFORM_REGISTRY.get(platform_key, PLATFORM_REGISTRY["generic_browser"])

    config_blob = {"job_board_url": req.url}
    if slug:
        config_blob["slug"] = slug

    # Workday endpoint derivation
    if adapter == "workday":
        # Strip /details/ and everything after (user may have pasted a specific job URL)
        clean_url = req.url.split("/details/")[0].split("/job/")[0]
        p = urlparse(clean_url)
        host_parts = p.netloc.lower().split(".")
        path_parts = [pp for pp in p.path.split("/") if pp and pp.lower() not in ("en-us", "en-gb", "en", "jobs", "job")]
        tenant = host_parts[0]
        shard = host_parts[1] if len(host_parts) > 2 else host_parts[0]
        site_path = path_parts[-1] if path_parts else tenant
        config_blob["endpoint_url"] = f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site_path}/jobs"
        config_blob["tenant"] = tenant
        config_blob["shard"] = shard
        config_blob["site_path"] = site_path

    url_hash = hashlib.md5(req.url.encode()).hexdigest()[:8]
    source_key = f"{meta['adapter']}_{_slugify(req.company)}_{url_hash}"
    parsed = urlparse(req.url)
    hostname = parsed.hostname or "unknown"

    with SessionLocal() as s:
        existing = s.execute(
            select(Source).where((Source.source_key == source_key) | (Source.base_url == req.url))
        ).scalars().first()

        if existing:
            return JSONResponse(status_code=409, content={"detail": f"Source already exists: {existing.employer_name} ({existing.adapter_name})", "id": existing.id})

        src = Source(
            source_key=source_key,
            employer_name=req.company,
            board_name=meta["board_name"],
            base_url=req.url,
            hostname=hostname,
            source_type=meta["source_type"],
            ats_family=meta["ats_family"],
            adapter_name=meta["adapter"],
            active=True,
            seed_type="manual_add",
            discovery_method="url_auto_detect",
            fingerprint=f"{hostname}|{meta['ats_family'] or meta['adapter']}",
            canonical_company_key=_slugify(req.company),
            config_blob=config_blob,
            capability_blob={},
        )
        s.add(src)
        s.commit()
        s.refresh(src)

        return AddSourceResponse(
            id=src.id,
            employer_name=src.employer_name,
            adapter_name=src.adapter_name,
            base_url=src.base_url,
            message=f"Added {src.employer_name} ({src.adapter_name})"
            + (f" — {result['job_count']} jobs detected" if result.get("job_count") else ""),
        )


# ── Queue Campaign ──

class QueueRequest(BaseModel):
    title: str
    company: str
    location: str | None = None
    country: str | None = None
    category: str | None = None
    sub_specialism: str | None = None
    url: str | None = None
    score: float | None = None
    board_url: str | None = None


_N8N_LEAD_INTAKE_URL = "https://antonyberou.app.n8n.cloud/webhook/prospero-lead-intake"
_N8N_QUEUE_DRAIN_URL = "https://antonyberou.app.n8n.cloud/webhook/job-queue-drain"


@app.post("/api/queue")
async def queue_campaign(req: QueueRequest):
    """Add a lead to the campaign queue, write to Google Sheet via n8n, trigger campaign generation."""
    from vacancysoft.db.models import ReviewQueueItem
    from datetime import datetime
    import httpx as _httpx

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
    if getattr(app.state, "redis", None):
        await app.state.redis.enqueue_job("process_lead", item_id, req.url, req.company, req.title)
    else:
        # Fallback: run in-process if Redis is unavailable
        import asyncio
        asyncio.ensure_future(_scrape_and_generate_dossier(item_id, req.url, req.company, req.title))

    return {"message": "Queued", "id": item_id}


_PLAYWRIGHT_SCRAPER_URL = "https://playwright-runner.bluecliff-1ceb6690.uksouth.azurecontainerapps.io/scrape"


async def _scrape_and_generate_dossier(item_id: str, url: str | None, company: str | None, title: str | None):
    """Background task: scrape the job advert, store the description, then generate the dossier."""
    import httpx as _httpx
    from vacancysoft.db.models import ReviewQueueItem, IntelligenceDossier

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


@app.get("/api/queue")
def list_queue():
    """List all queued campaign leads.

    A lead is only reported as 'ready' if an IntelligenceDossier actually exists
    for its enriched job. Otherwise we downgrade the reported status to
    'generating' so downstream consumers (e.g. the Campaign Builder) don't try
    to generate a campaign before the dossier is persisted.
    """
    from vacancysoft.db.models import ReviewQueueItem, IntelligenceDossier
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


@app.post("/api/queue/{item_id}/send")
async def send_to_campaign(item_id: str):
    """Send a queued lead to n8n for campaign generation."""
    from vacancysoft.db.models import ReviewQueueItem
    from datetime import datetime
    import httpx as _httpx

    with SessionLocal() as s:
        item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
        if not item:
            raise HTTPException(status_code=404, detail="Not found")
        evidence = item.evidence_blob or {}

        # Update status
        item.status = "generating"
        s.commit()

    # Send to n8n — write to Google Sheet
    now = datetime.utcnow().strftime("%Y-%m-%d")
    job_ref = evidence.get("job_ref", f"lead-{item_id[:10]}")
    sheet_payload = {
        "Job URL": evidence.get("url", ""),
        "Job Title": evidence.get("title", ""),
        "Job Ref": job_ref,
        "Category": evidence.get("category", ""),
        "Sub Specialism": evidence.get("sub_specialism", ""),
        "Company": evidence.get("company", ""),
        "Location": evidence.get("location", ""),
        "Country": evidence.get("country", ""),
        "Salary": "",
        "Contract Type": "",
        "Date Posted": now,
        "Job Board URL": "",
        "Platform": "prospero",
        "Date Scraped": now,
    }

    return {"message": "Status updated to generating", "id": item_id}


@app.api_route("/api/queue/callback", methods=["GET", "POST"])
async def queue_callback(request):
    """Called by n8n when campaign generation is complete. Updates lead status to 'ready'."""
    from vacancysoft.db.models import ReviewQueueItem

    req = {}
    try:
        req = await request.json()
    except Exception:
        pass
    if not req:
        req = dict(request.query_params)

    if not req:
        return {"message": "No data"}

    job_ref = req.get("jobRef", "")
    status = req.get("status", "ready")

    with SessionLocal() as s:
        # Find the queue item by job_ref in evidence_blob
        items = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.queue_type == "campaign")).scalars().all()
        updated = 0
        for item in items:
            evidence = item.evidence_blob or {}
            if evidence.get("job_ref") == job_ref:
                item.status = status
                updated += 1
        s.commit()

    return {"message": f"Updated {updated} items to {status}", "jobRef": job_ref}


@app.delete("/api/queue/{item_id}")
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


class ScrapeResponse(BaseModel):
    source_key: str
    employer_name: str
    jobs_found: int
    status: str
    removed: bool = False


_API_ONLY_ADAPTERS = {"workday", "greenhouse", "workable", "ashby", "smartrecruiters", "lever",
                      "pinpoint", "bamboohr", "teamtailor", "personio", "recruitee", "jazzhr",
                      "silkroad", "reed", "adzuna", "google_jobs", "efinancialcareers"}


@app.post("/api/sources/{source_id}/scrape")
async def scrape_source_endpoint(source_id: int):
    """Scrape a source. API-based adapters go through Redis worker, browser-based run inline."""
    with SessionLocal() as s:
        src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        employer_name = src.employer_name
        adapter_name = src.adapter_name

    if adapter_name in _API_ONLY_ADAPTERS and getattr(app.state, "redis", None):
        await app.state.redis.enqueue_job("scrape_source", source_id)
        return {"message": f"Scrape queued for {employer_name}", "source_id": source_id, "status": "queued"}
    else:
        # Browser-based adapters run inline (they need local Playwright)
        from vacancysoft.worker.tasks import scrape_source as _scrape
        asyncio.ensure_future(_scrape({}, source_id))
        return {"message": f"Scraping {employer_name} (browser)", "source_id": source_id, "status": "queued"}


@app.post("/api/sources/{source_id}/diagnose")
async def diagnose_source(source_id: int):
    """Diagnose a failing or empty source: re-detect platform, check URL, auto-fix config."""
    from vacancysoft.api.source_detector import detect_and_validate

    with SessionLocal() as s:
        src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        employer_name = src.employer_name
        current_adapter = src.adapter_name
        current_url = src.base_url
        raw_count = s.execute(select(func.count()).select_from(RawJob).where(RawJob.source_id == source_id)).scalar() or 0

    diagnosis: dict[str, Any] = {
        "source_id": source_id,
        "company": employer_name,
        "current_adapter": current_adapter,
        "current_url": current_url,
        "raw_jobs": raw_count,
        "issues": [],
        "actions_taken": [],
        "status": "ok",
    }

    # Step 1: Check if the URL is reachable
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(current_url)
            diagnosis["http_status"] = resp.status_code
            diagnosis["final_url"] = str(resp.url)

            if resp.status_code == 403:
                diagnosis["issues"].append("URL returns 403 Forbidden — site may be blocking bots")
                diagnosis["status"] = "blocked"
            elif resp.status_code == 404:
                diagnosis["issues"].append("URL returns 404 — page not found, URL may have changed")
                diagnosis["status"] = "dead_url"
            elif resp.status_code >= 500:
                diagnosis["issues"].append(f"URL returns {resp.status_code} — server error")
                diagnosis["status"] = "server_error"

            # Check if redirected to a different domain
            if str(resp.url) != current_url and resp.url.host not in current_url:
                diagnosis["issues"].append(f"URL redirects to different domain: {resp.url}")
    except Exception as exc:
        diagnosis["issues"].append(f"URL unreachable: {type(exc).__name__}: {exc}")
        diagnosis["status"] = "unreachable"
        return diagnosis

    # Step 2: Deep platform detection — check page body for platform markers
    import re as _re
    _PLATFORM_MARKERS = {
        "workday": (r"myworkdayjobs\.com|workday", "workday"),
        "greenhouse": (r"greenhouse\.io|boards\.greenhouse|gh_jid=", "greenhouse"),
        "lever": (r"jobs\.lever\.co|lever\.co/", "lever"),
        "oracle": (r"oraclecloud\.(com|eu)|oracle.*hcmUI|recruitingCEJobRequisition", "oracle"),
        "successfactors": (r"successfactors\.(com|eu)", "successfactors"),
        "phenom": (r"phenom\.com|phenom", "phenom"),
        "teamtailor": (r"teamtailor\.com|teamtailor", "teamtailor"),
        "smartrecruiters": (r"smartrecruiters\.com", "smartrecruiters"),
        "ashby": (r"ashbyhq\.com", "ashby"),
        "bamboohr": (r"bamboohr\.com", "bamboohr"),
        "icims": (r"icims\.com", "icims"),
        "pinpoint": (r"pinpointhq\.com", "pinpoint"),
        "workable": (r"workable\.com", "workable"),
        "hibob": (r"hibob\.com", "hibob"),
        "taleo": (r"taleo\.net", "taleo"),
        "eightfold": (r"eightfold\.ai", "eightfold"),
    }

    body_detected = None
    if resp.status_code == 200:
        body = resp.text[:10000].lower()
        for marker_name, (pattern, adapter_name) in _PLATFORM_MARKERS.items():
            if _re.search(pattern, body, _re.I):
                body_detected = adapter_name
                break

    try:
        detection = await detect_and_validate(current_url, timeout=15)
        detected_adapter = body_detected or detection.get("adapter", "generic_site")
        detected_slug = detection.get("slug")
        diagnosis["detected_adapter"] = detected_adapter
        diagnosis["detected_slug"] = detected_slug

        if detected_adapter != current_adapter:
            diagnosis["issues"].append(f"Platform mismatch: configured as '{current_adapter}' but detected as '{detected_adapter}'")

            # Auto-fix: update the source
            with SessionLocal() as s:
                src = s.execute(select(Source).where(Source.id == source_id)).scalar_one()
                src.adapter_name = detected_adapter
                src.ats_family = detected_adapter
                config = dict(src.config_blob or {})
                config["job_board_url"] = current_url
                if detected_slug:
                    config["slug"] = detected_slug
                src.config_blob = config
                s.commit()

            diagnosis["actions_taken"].append(f"Updated adapter from '{current_adapter}' to '{detected_adapter}'")
            if detected_slug:
                diagnosis["actions_taken"].append(f"Set slug to '{detected_slug}'")
            diagnosis["status"] = "fixed"

            # Re-scrape with new config
            if getattr(app.state, "redis", None):
                await app.state.redis.enqueue_job("scrape_source", source_id)
                diagnosis["actions_taken"].append("Re-scrape queued")

    except Exception as exc:
        diagnosis["issues"].append(f"Platform detection failed: {exc}")

    # Step 3: Check for missing config
    if not diagnosis.get("actions_taken"):
        with SessionLocal() as s:
            src = s.execute(select(Source).where(Source.id == source_id)).scalar_one()
            config = src.config_blob or {}

            if current_adapter not in ("generic_site",) and not config.get("slug") and not config.get("job_board_url"):
                diagnosis["issues"].append("Missing slug and job_board_url in config")
                diagnosis["status"] = "bad_config"

            if not diagnosis["issues"]:
                diagnosis["issues"].append("No issues detected — source may simply have no relevant jobs")
                # Try a re-scrape anyway
                if getattr(app.state, "redis", None):
                    await app.state.redis.enqueue_job("scrape_source", source_id)
                    diagnosis["actions_taken"].append("Re-scrape queued")

    return diagnosis


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int):
    """Remove a source and all its jobs from the DB."""
    from vacancysoft.db.models import (
        ClassificationResult, SourceRun, ExtractionAttempt,
        IntelligenceDossier, CampaignOutput, ReviewQueueItem,
    )
    from sqlalchemy import delete as sa_delete
    with SessionLocal() as s:
        src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        name = src.employer_name
        # Clean up all related data (deepest children first)
        raw_ids = [r.id for r in s.execute(select(RawJob).where(RawJob.source_id == source_id)).scalars()]
        if raw_ids:
            ej_ids = [e.id for e in s.execute(select(EnrichedJob).where(EnrichedJob.raw_job_id.in_(raw_ids))).scalars()]
            if ej_ids:
                # Dossiers and campaigns
                dossier_ids = [d.id for d in s.execute(select(IntelligenceDossier).where(IntelligenceDossier.enriched_job_id.in_(ej_ids))).scalars()]
                if dossier_ids:
                    s.execute(sa_delete(CampaignOutput).where(CampaignOutput.dossier_id.in_(dossier_ids)))
                s.execute(sa_delete(IntelligenceDossier).where(IntelligenceDossier.enriched_job_id.in_(ej_ids)))
                s.execute(sa_delete(ReviewQueueItem).where(ReviewQueueItem.enriched_job_id.in_(ej_ids)))
                s.execute(sa_delete(ScoreResult).where(ScoreResult.enriched_job_id.in_(ej_ids)))
                s.execute(sa_delete(ClassificationResult).where(ClassificationResult.enriched_job_id.in_(ej_ids)))
                s.execute(sa_delete(EnrichedJob).where(EnrichedJob.id.in_(ej_ids)))
            s.execute(sa_delete(RawJob).where(RawJob.source_id == source_id))
        # Remove extraction attempts and runs
        s.execute(sa_delete(ExtractionAttempt).where(ExtractionAttempt.source_id == source_id))
        s.execute(sa_delete(SourceRun).where(SourceRun.source_id == source_id))
        s.delete(src)
        s.commit()
    return {"message": f"Removed {name}", "id": source_id}


# ── Mark company as agency ──


class MarkAgencyRequest(BaseModel):
    company: str


class MarkAgencyResponse(BaseModel):
    added: bool
    deleted_jobs: int
    deleted_classifications: int
    deleted_scores: int
    deleted_dossiers: int
    deleted_queue_items: int


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
