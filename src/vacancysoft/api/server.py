"""Prospero API — lightweight FastAPI server wrapping the scraping engine."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, text

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source, RawJob, EnrichedJob, ScoreResult
from vacancysoft.api.ledger import (
    _AGGREGATOR_ADAPTERS,
    _CORE_MARKETS,
    _extract_employer_from_payload,
    clear_ledger_caches,
)
from vacancysoft.api.routes import leads as leads_routes
from vacancysoft.api.routes import sources as sources_routes
from vacancysoft.api.schemas import (
    AddCompanyCandidate,
    AddCompanyRequest,
    AddCompanyResponse,
    MarkAgencyRequest,
    MarkAgencyResponse,
)

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

    clear_ledger_caches()

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
