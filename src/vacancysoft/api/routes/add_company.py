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
from vacancysoft.api.schemas import AddCompanyCandidate, AddCompanyRequest, AddCompanyResponse
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source


router = APIRouter(tags=["add-company"])


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
