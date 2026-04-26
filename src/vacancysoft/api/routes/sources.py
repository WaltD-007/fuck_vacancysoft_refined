"""Source management endpoints.

Covers everything under `/api/sources/*` except the Coresignal
"add-company" flow (that lives in `routes/add_company.py`):

  GET    /api/sources
  GET    /api/sources/{source_id}/jobs
  POST   /api/sources/{source_id}/scrape
  POST   /api/sources/{source_id}/diagnose
  DELETE /api/sources/{source_id}

URL-driven add (POST /api/sources, POST /api/sources/detect) was removed
in favour of the CoreSignal-backed Add Company flow. The CLI command
`prospero db add-source <url>` remains for operator-only use; it calls
`detect_and_validate()` directly without going through HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from vacancysoft.api.ledger import (
    _CATEGORY_LABELS,
    _get_cached_ledger,
    _sources_cache,
    _SOURCES_CACHE_TTL,
)
from vacancysoft.api.schemas import (
    ScoredJobOut,
    SourceOut,
)
from vacancysoft.api.source_detector import (
    UnsafeURLError,
    _validate_outgoing_request,
    detect_and_validate,
    validate_public_url,
)
from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source
from vacancysoft.source_registry.config_seed_loader import PLATFORM_REGISTRY


router = APIRouter(tags=["sources"])


ADAPTER_MAP = {
    "greenhouse": "greenhouse", "workday": "workday", "lever": "lever",
    "icims": "icims", "ashby": "ashby", "smartrecruiters": "smartrecruiters",
    "workable": "workable", "oracle_cloud": "oracle", "successfactors": "successfactors",
    "eightfold": "eightfold", "pinpoint": "pinpoint", "hibob": "hibob",
    "taleo": "taleo", "teamtailor": "teamtailor", "generic_site": "generic_browser",
}


_API_ONLY_ADAPTERS = {"workday", "greenhouse", "workable", "ashby", "smartrecruiters", "lever",
                      "pinpoint", "bamboohr", "teamtailor", "personio", "recruitee", "jazzhr",
                      "silkroad", "reed", "adzuna", "google_jobs", "efinancialcareers"}


@router.get("/api/sources", response_model=list[SourceOut])
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
            sub_specialisms_by_country=card.get("sub_specialisms_by_country", {}),
            aggregator_hits=card["aggregator_hits"],
            employment_types=card.get("employment_types", {}),
            last_run_status=card["last_run_status"],
            last_run_error=card["last_run_error"],
        )
        for card in ledger
    ]
    _sources_cache[cache_key] = (_time.time(), result)
    return result


@router.get("/api/sources/{source_id}/jobs", response_model=list[ScoredJobOut])
def get_source_jobs(
    source_id: int,
    category: str | None = None,
    company: str | None = None,
    country: str | None = None,
    sub_specialism: list[str] | None = Query(None),
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
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category

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
                ClassificationResult.sub_specialism,
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

    # OR across sub_specialism filter values — matches the chip semantics on
    # the Sources page. sub_specialism is now read straight from the
    # ClassificationResult column (2026-04-20 change); previously this was
    # recomputed via map_sub_specialism() against configs/legacy_routing.yaml.
    sub_allowed: set[str] | None = (
        {s for s in sub_specialism if s} if sub_specialism else None
    )

    result: list[ScoredJobOut] = []
    for r in rows:
        title = r[1] or ""
        cat_label = map_category(r[4], routing)
        sub_spec = r[5] or "Other"
        if sub_allowed is not None and sub_spec not in sub_allowed:
            continue
        result.append(ScoredJobOut(
            id=r[0],
            title=title,
            company=target["employer_display"],
            location=r[2],
            country=r[3],
            category=cat_label,
            sub_specialism=sub_spec,
            score=round(r[6], 1) if r[6] else None,
            url=r[7],
        ))
    return result


@router.post("/api/sources/{source_id}/scrape")
async def scrape_source_endpoint(source_id: int, request: Request):
    """Scrape a source. API-based adapters go through Redis worker, browser-based run inline."""
    with SessionLocal() as s:
        src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        employer_name = src.employer_name
        adapter_name = src.adapter_name

    if adapter_name in _API_ONLY_ADAPTERS and getattr(request.app.state, "redis", None):
        await request.app.state.redis.enqueue_job("scrape_source", source_id)
        return {"message": f"Scrape queued for {employer_name}", "source_id": source_id, "status": "queued"}
    else:
        # Browser-based adapters run inline (they need local Playwright)
        from vacancysoft.worker.tasks import scrape_source as _scrape
        asyncio.ensure_future(_scrape({}, source_id))
        return {"message": f"Scraping {employer_name} (browser)", "source_id": source_id, "status": "queued"}


@router.post("/api/sources/{source_id}/diagnose")
async def diagnose_source(source_id: int, request: Request):
    """Diagnose a failing or empty source: re-detect platform, check URL, auto-fix config."""
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
    # SSRF defence: current_url comes from the DB (added via add_source which
    # now validates), but we re-validate here in case the row predates the fix
    # or was inserted by a script. Bad rows get a 400 instead of being probed.
    import httpx as _httpx
    try:
        await validate_public_url(current_url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=f"Stored source URL is not safe to fetch: {exc}")
    try:
        async with _httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            event_hooks={"request": [_validate_outgoing_request]},
        ) as client:
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

            # Auto-fix: update the source.
            # Previously (pre-2026-04-22) this wrote `ats_family = detected_adapter`
            # directly, which desynced the two columns for cases where the
            # canonical ats_family label differs from the adapter key (e.g.
            # adapter='oracle' vs ats_family='oracle_cloud', or
            # adapter='generic_site' vs ats_family='generic_browser'). Look up
            # the canonical pair via PLATFORM_REGISTRY like `add_source` does
            # at line 272-273 so the two columns stay aligned.
            with SessionLocal() as s:
                src = s.execute(select(Source).where(Source.id == source_id)).scalar_one()
                platform_key = ADAPTER_MAP.get(detected_adapter, "generic_browser")
                meta = PLATFORM_REGISTRY.get(platform_key, PLATFORM_REGISTRY["generic_browser"])
                src.adapter_name = meta["adapter"]
                src.ats_family = meta["ats_family"]
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
            if getattr(request.app.state, "redis", None):
                await request.app.state.redis.enqueue_job("scrape_source", source_id)
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
                if getattr(request.app.state, "redis", None):
                    await request.app.state.redis.enqueue_job("scrape_source", source_id)
                    diagnosis["actions_taken"].append("Re-scrape queued")

    return diagnosis


@router.delete("/api/sources/{source_id}")
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
