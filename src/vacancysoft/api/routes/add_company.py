"""Coresignal "Add a Company" wizard endpoints.

Two-phase flow for adding a company card via Coresignal's taxonomy
sweep:

  POST /api/sources/add-company/search   — preview; returns candidate
                                           employer names or "no_jobs"
                                           / "exists"
  POST /api/sources/add-company/confirm  — commit; creates the Source
                                           row and runs the capped scrape

Helpers (`_addcompany_*`) are Coresignal-specific and live here with
the handlers that use them. Extracted verbatim from `api/server.py`
during the Week 4 split.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import bindparam, func, select, text

from vacancysoft.api.ledger import _AGGREGATOR_ADAPTERS, _CORE_MARKETS, clear_ledger_caches
from vacancysoft.api.schemas import (
    AddCompanyCandidate,
    AddCompanyRequest,
    AddCompanyResponse,
    AddCompanyUpdateCommitResponse,
    AddCompanyUpdateLead,
    AddCompanyUpdatePreviewResponse,
    AddCompanyUpdateRequest,
)
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source


router = APIRouter(tags=["add-company"])


def _addcompany_slugify(s: str) -> str:
    return "_".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def _addcompany_source_key(company: str) -> str:
    import hashlib as _hash
    url_hash = _hash.md5(f"coresignal:{company}".encode()).hexdigest()[:8]
    return f"coresignal_{_addcompany_slugify(company)}_{url_hash}"


def _addcompany_scoped_source_key(company: str, scope: str) -> str:
    """Source-key for a location-scoped CoreSignal source (update flow).

    `scope` is a short tag like "uk" or "ny" so each (employer, geo) pair
    gets its own Source row + independent SourceRun history.
    """
    import hashlib as _hash
    url_hash = _hash.md5(f"coresignal:{company}:{scope}".encode()).hexdigest()[:8]
    return f"coresignal_{_addcompany_slugify(company)}_{scope}_{url_hash}"


def _best_lead_url(record: dict | None, fallback: str | None = None) -> str | None:
    """Find the best http(s) URL for a CoreSignal record.

    CoreSignal preview responses label the advert URL differently across
    sources — we widen the search across a handful of field names and also
    dive into `job_sources[]`. Values are only returned when they start with
    http:// or https:// so malformed strings don't become broken links.
    """
    if isinstance(record, dict):
        for field in ("external_url", "apply_url", "url", "job_url", "source_url"):
            val = record.get(field)
            if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
                return val.strip()
        sources = record.get("job_sources")
        if isinstance(sources, list):
            for src in sources:
                if isinstance(src, dict):
                    for field in ("url", "apply_url", "external_url"):
                        val = src.get(field)
                        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
                            return val.strip()
    return fallback


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
                company=company, location={"country": country}, since=since, title_phrases=phrases,
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

    Each candidate: {'employer_name','jobs_count','sample_title','sample_location','sample_url'}.
    The sample URL lets the user open one representative job advert before picking
    which employer to add — preview responses already carry the URL, so we capture
    it here rather than paying for /collect.

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
    samples: dict[str, tuple[str, str, str | None]] = {}  # {employer_lower: (title, location, url)}
    total_rows = 0
    seen_ids: set[int] = set()

    async with _httpx.AsyncClient(
        timeout=30,
        headers={"apikey": api_key, "accept": "application/json", "Content-Type": "application/json"},
    ) as client:
        for country in countries:
            for cat_name, cat_terms in by_cat.items():
                query = _build_taxonomy_query(
                    company=company, location={"country": country}, since=since, title_phrases=cat_terms,
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
                            _best_lead_url(row),
                        )
                    total_rows += 1

    candidates: list[dict] = []
    for employer, n in employer_counts.most_common():
        title, loc, url = samples.get(employer.lower(), ("", "", None))
        candidates.append({
            "employer_name": employer,
            "jobs_count": n,
            "sample_title": title or None,
            "sample_location": loc or None,
            "sample_url": url,
        })
    # total unique jobs seen (capped by preview's 20/query but deduped across calls)
    return len(seen_ids), candidates


@router.post("/api/sources/add-company/search", response_model=AddCompanyResponse)
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
                can_update=True,
                message=f"A direct card for {existing_direct.employer_name} already exists — running a CoreSignal sweep now to find any new leads.",
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
            sample_url=c.get("sample_url"),
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


@router.post("/api/sources/add-company/confirm", response_model=AddCompanyResponse)
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


def _lookup_direct_source(source_id: int) -> Source | None:
    """Return the active direct (non-aggregator) Source row for an update flow."""
    with SessionLocal() as s:
        return s.execute(
            select(Source).where(
                Source.id == source_id,
                Source.active.is_(True),
                Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
            )
        ).scalar_one_or_none()


@router.post("/api/sources/add-company/update-preview", response_model=AddCompanyUpdatePreviewResponse)
async def add_company_update_preview(req: AddCompanyUpdateRequest):
    """Preview-mode CoreSignal sweep scoped to an existing direct card.

    Does NOT persist — the user reviews the leads list and either commits (via
    /update-commit) or dismisses. Uses the adapter's `use_preview=True` mode so
    each run is just a handful of preview calls (no /collect credits).

    Leads are deduped against RawJobs already captured by any CoreSignal source
    attached to this employer, so the UI only shows genuinely new rows.
    """
    import os
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    api_key = (os.getenv("CORESIGNAL_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="CORESIGNAL_API_KEY not configured on server")

    src = _lookup_direct_source(req.source_id)
    if not src:
        return AddCompanyUpdatePreviewResponse(
            status="not_found", source_id=req.source_id, employer_name="",
            leads_found=0,
            message=f"No active direct card found with id={req.source_id}.",
        )
    employer = (src.employer_name or "").strip()
    if not employer:
        return AddCompanyUpdatePreviewResponse(
            status="error", source_id=req.source_id, employer_name="",
            leads_found=0, message="Direct card has no employer_name set.",
        )

    from vacancysoft.adapters.coresignal import CoresignalAdapter
    adapter = CoresignalAdapter()
    days_back = max(int(req.days_back), 1)
    since = _dt.now(_tz.utc) - _td(days=days_back)
    config = {
        "api_key": api_key,
        "company_filter": employer,
        "use_full_taxonomy": True,
        "use_preview": True,
        "max_age_days": days_back,
    }

    try:
        page = await adapter.discover(config, since=since)
    except Exception as e:  # adapter-level failure — surface as preview error
        return AddCompanyUpdatePreviewResponse(
            status="error", source_id=req.source_id, employer_name=employer,
            leads_found=0, message=f"Preview failed: {type(e).__name__}: {e}",
        )

    # Dedup against external_job_ids already stored for any Coresignal source
    # that covers this employer. Skip dedup silently on any DB error.
    seen_external_ids: set[str] = set()
    try:
        from vacancysoft.db.models import RawJob
        with SessionLocal() as s:
            coresignal_source_ids = [r[0] for r in s.execute(
                select(Source.id).where(
                    Source.adapter_name == "coresignal",
                    func.lower(Source.employer_name) == employer.lower(),
                )
            ).all()]
            if coresignal_source_ids:
                rows = s.execute(
                    select(RawJob.external_job_id).where(
                        RawJob.source_id.in_(coresignal_source_ids),
                        RawJob.external_job_id.is_not(None),
                    )
                ).all()
                seen_external_ids = {r[0] for r in rows if r[0]}
    except Exception:
        pass

    leads: list[AddCompanyUpdateLead] = []
    for j in page.jobs:
        ext = j.external_job_id or ""
        if ext and ext in seen_external_ids:
            continue
        summary = j.summary_raw
        leads.append(AddCompanyUpdateLead(
            external_id=ext,
            title=j.title_raw or "",
            company=(j.provenance or {}).get("company"),
            location=j.location_raw,
            url=_best_lead_url(j.listing_payload, j.apply_url or j.discovered_url),
            posted_at=j.posted_at_raw,
            summary=summary[:300] if summary else None,
        ))

    if not leads:
        return AddCompanyUpdatePreviewResponse(
            status="no_jobs", source_id=req.source_id, employer_name=employer,
            leads_found=0,
            message=f"No new CoreSignal leads found for {employer} in the last {days_back} days.",
        )

    return AddCompanyUpdatePreviewResponse(
        status="ready", source_id=req.source_id, employer_name=employer,
        leads_found=len(leads), leads=leads,
        message=f"Found {len(leads)} new potential leads for {employer}.",
    )


@router.post("/api/sources/add-company/update-commit", response_model=AddCompanyUpdateCommitResponse)
async def add_company_update_commit(req: AddCompanyUpdateRequest):
    """Commit an update sweep by running the full CoreSignal pipeline.

    Finds (or creates) a CoreSignal Source scoped to the direct card's
    employer, then invokes the ARQ scrape_source task. Discovered jobs are
    stored as RawJobs under the CoreSignal source and the ledger merge
    surfaces them on the direct card automatically — no manual attachment
    needed.
    """
    import os

    api_key = (os.getenv("CORESIGNAL_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="CORESIGNAL_API_KEY not configured on server")

    src = _lookup_direct_source(req.source_id)
    if not src:
        return AddCompanyUpdateCommitResponse(
            status="not_found", source_id=req.source_id, employer_name="",
            message=f"No active direct card found with id={req.source_id}.",
        )
    employer = (src.employer_name or "").strip()
    if not employer:
        return AddCompanyUpdateCommitResponse(
            status="error", source_id=req.source_id, employer_name="",
            message="Direct card has no employer_name set.",
        )

    days_back = max(int(req.days_back), 1)

    # Two scope-specific Coresignal sources so UK and NY run independently:
    # each has its own SourceRun history and can be re-fired from the sources
    # page without touching the other. Ledger merge still surfaces both
    # streams on the same direct card via normalised employer name.
    location_scopes: list[tuple[str, list[dict[str, str]], str]] = [
        ("uk", [{"country": "United Kingdom"}], "Coresignal (UK)"),
        ("ny", [{"country": "United States", "city": "New York"}], "Coresignal (New York)"),
    ]

    coresignal_source_ids: list[int] = []
    with SessionLocal() as s:
        for scope, locations, board_name in location_scopes:
            config = {
                "job_board_url": "https://api.coresignal.com/cdapi/v2/job_multi_source",
                "company_filter": employer,
                "use_full_taxonomy": True,
                "use_preview": False,  # commit: fetch full records for the ingestion pipeline
                "max_age_days": days_back,
                "locations": locations,
                "request_delay": 0.1,
            }
            key = _addcompany_scoped_source_key(employer, scope)
            existing = s.execute(
                select(Source).where(Source.source_key == key)
            ).scalar_one_or_none()
            if existing:
                merged = dict(existing.config_blob or {})
                merged.update(config)
                existing.config_blob = merged
                existing.active = True
                s.commit()
                s.refresh(existing)
                coresignal_source_ids.append(existing.id)
            else:
                new_cs = Source(
                    source_key=key,
                    employer_name=employer,
                    board_name=board_name,
                    base_url="https://api.coresignal.com/cdapi/v2/job_multi_source",
                    hostname="api.coresignal.com",
                    source_type="ats_api",
                    ats_family="coresignal",
                    adapter_name="coresignal",
                    active=True,
                    seed_type="add_company_update",
                    discovery_method="add_company",
                    fingerprint=f"coresignal|{_addcompany_slugify(employer)}|{scope}",
                    canonical_company_key=_addcompany_slugify(employer),
                    config_blob=config,
                    capability_blob={},
                )
                s.add(new_cs)
                s.commit()
                s.refresh(new_cs)
                coresignal_source_ids.append(new_cs.id)

    # Kick off both scrapes in parallel. `return_exceptions=True` lets one
    # geo fail without cancelling the other — we surface the partial result
    # in the response so the ledger count still reflects whatever landed.
    import asyncio
    from vacancysoft.worker.tasks import scrape_source as _scrape_task

    results = await asyncio.gather(
        *(_scrape_task({}, sid) for sid in coresignal_source_ids),
        return_exceptions=True,
    )
    errors = [
        f"source {coresignal_source_ids[i]} ({location_scopes[i][0]}): {type(r).__name__}: {r}"
        for i, r in enumerate(results) if isinstance(r, Exception)
    ]

    clear_ledger_caches()

    with SessionLocal() as s:
        leads_added = s.execute(text(
            """
            SELECT COUNT(*) FROM classification_results cr
            JOIN enriched_jobs ej ON ej.id = cr.enriched_job_id
            JOIN raw_jobs rj ON rj.id = ej.raw_job_id
            WHERE rj.source_id IN :sids
              AND cr.primary_taxonomy_key IN :core
            """
        ).bindparams(
            bindparam("sids", expanding=True),
            bindparam("core", expanding=True),
        ), {
            "sids": coresignal_source_ids, "core": list(_CORE_MARKETS),
        }).scalar() or 0

    if errors:
        return AddCompanyUpdateCommitResponse(
            status="error", source_id=req.source_id, employer_name=employer,
            coresignal_source_ids=coresignal_source_ids,
            leads_added=int(leads_added),
            message=(
                f"Update ran with {int(leads_added)} lead(s) added "
                f"but one or more scrapes failed — {'; '.join(errors)}"
            ),
        )
    return AddCompanyUpdateCommitResponse(
        status="ok", source_id=req.source_id, employer_name=employer,
        coresignal_source_ids=coresignal_source_ids,
        leads_added=int(leads_added),
        message=(
            f"Added {int(leads_added)} new lead(s) to {employer} via CoreSignal "
            f"(UK + NY ran independently)."
        ),
    )
