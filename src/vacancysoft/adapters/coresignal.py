"""
Coresignal Multi-source Jobs API adapter.

Uses the ElasticSearch DSL search endpoint to find job IDs, then /collect to
fetch full records (including description, company, location, etc.). Treated
as an aggregator — jobs are merged into existing employer cards or populate
virtual cards for employers not already in the DB (see server.py list_sources).

Requires CORESIGNAL_API_KEY env var or `api_key` in source_config.

Docs:
  https://docs.coresignal.com/jobs-api/multi-source-jobs-api
  https://docs.coresignal.com/jobs-api/multi-source-jobs-api/elasticsearch-dsl/postman-tutorial
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    PageCallback,
    SourceAdapter,
)

API_BASE = "https://api.coresignal.com/cdapi/v2/job_multi_source"
SEARCH_ENDPOINT = f"{API_BASE}/search/es_dsl"
PREVIEW_ENDPOINT = f"{API_BASE}/search/es_dsl/preview"
COLLECT_ENDPOINT = f"{API_BASE}/collect"  # + /{id}

DEFAULT_SEARCH_TERMS = [
    "risk", "quant", "quantitative", "compliance",
    "audit", "legal", "cyber", "financial crime",
]

# Standard location scope for every CoreSignal search: UK and New York City.
# Each entry is a dict so we can narrow to a specific city within a country.
# Override via `source_config["locations"]`.
DEFAULT_LOCATIONS: list[dict[str, str]] = [
    {"country": "United Kingdom"},
    {"country": "United States", "city": "New York"},
]
DEFAULT_MAX_PER_TERM = 100  # max IDs retrieved per search term/location combo
DEFAULT_REQUEST_DELAY = 0.1  # 18 req/sec cap on search endpoint
DEFAULT_MAX_AGE_DAYS = 7  # only return ads posted within this many days

# Date field on Coresignal job records that represents when the ORIGINAL advert
# was posted by the employer (not when Coresignal scraped/indexed the job).
# `created_at` is the scrape timestamp — avoid that for freshness filtering.
POST_DATE_FIELD = "date_posted"


def _location_label(location: dict) -> str:
    """Human-readable label for diagnostics/provenance (e.g. 'New York, United States')."""
    parts = [location.get("city"), location.get("country")]
    return ", ".join(p for p in parts if p)


def _location_clauses(location: dict) -> list[dict]:
    """Return the ES `must` clauses for a location dict (country + optional city).

    `match_phrase` on both fields prevents token-match leaks (e.g. 'United Kingdom'
    matching US jobs whose location text contains 'United' or 'Kingdom', or
    'New York' matching the state when we want only the city).
    """
    clauses: list[dict] = []
    country = location.get("country")
    if country:
        clauses.append({"match_phrase": {"country": country}})
    city = location.get("city")
    if city:
        clauses.append({"match_phrase": {"city": city}})
    return clauses


def _build_es_query(term: str, location: dict, since: datetime | None) -> dict:
    """Build an ElasticSearch DSL query filtering by title keywords + location + date.

    Date filter uses `date_posted` (original advert post date), NOT `created_at`
    (which is when Coresignal scraped the job). Format: `yyyy-MM-dd HH:mm:ss`.
    """
    must: list[dict] = [
        {"match_phrase": {"title": term}} if " " in term else {"match": {"title": term}},
        *_location_clauses(location),
    ]
    if since is not None:
        must.append({
            "range": {
                POST_DATE_FIELD: {"gte": since.strftime("%Y-%m-%d %H:%M:%S")}
            }
        })
    return {"query": {"bool": {"must": must}}}


def _build_taxonomy_query(
    *,
    company: str | None,
    location: dict,
    since: datetime | None,
    title_phrases: list[str],
) -> dict:
    """Build an ES DSL query matching ANY of the taxonomy title phrases for a
    specific company, location, and date window.

    Used by the Add Company flow to sweep the full taxonomy against one employer.
    """
    shoulds = [
        {"match_phrase": {"title": t}} if " " in t else {"match": {"title": t}}
        for t in title_phrases
    ]
    must: list[dict] = list(_location_clauses(location))
    if company:
        must.append({"match_phrase": {"company_name": company}})
    if since is not None:
        must.append({
            "range": {POST_DATE_FIELD: {"gte": since.strftime("%Y-%m-%d %H:%M:%S")}}
        })
    return {"query": {"bool": {
        "must": must,
        "should": shoulds,
        "minimum_should_match": 1,
    }}}


def load_taxonomy_title_phrases() -> list[str]:
    """Flatten all taxonomy title-phrase keywords (across all 7 core markets and Other)
    from `configs/legacy_routing.yaml` into a single deduplicated list.
    """
    grouped = load_taxonomy_by_category()
    phrases: set[str] = set()
    for terms in grouped.values():
        phrases.update(terms)
    return sorted(phrases)


def load_taxonomy_by_category() -> dict[str, list[str]]:
    """Return {category_name: [title_phrases]} grouped from configs/legacy_routing.yaml."""
    import yaml
    from pathlib import Path

    candidate_paths = [
        Path.cwd() / "configs" / "legacy_routing.yaml",
        Path(__file__).resolve().parents[3] / "configs" / "legacy_routing.yaml",
    ]
    yaml_path = next((p for p in candidate_paths if p.exists()), None)
    if yaml_path is None:
        return {}

    with yaml_path.open() as f:
        data = yaml.safe_load(f) or {}

    rules = data.get("sub_specialism_keywords") or data.get("classification_rules") or data.get("rules") or {}

    grouped: dict[str, list[str]] = {}
    for category, subspecs in rules.items():
        if not isinstance(subspecs, dict):
            continue
        phrases: set[str] = set()
        for subspec, terms in subspecs.items():
            if not isinstance(terms, list):
                continue
            for t in terms:
                t = str(t).strip().lower()
                if t:
                    phrases.add(t)
        if phrases:
            grouped[category] = sorted(phrases)
    return grouped


# Coresignal returns these as `company_name` for jobs sourced from re-posting
# aggregators. The real employer is embedded in the title (typically after " - ")
# or in the source URL. Mirrors the existing Adzuna/Reed/eFinancial aggregator
# treatment in server.py.
_AGGREGATOR_COMPANY_LABELS: set[str] = {
    "jobs via efinancialcareers",
    "jobs via dice",
    "jobs via indeed",
    "jobs via linkedin",
    "jobs via talentup",
    "jobster",
    "dataannotation",
    "talentally",
    "mygwork - lgbtq+ business community",
    "mygwork",
}

# Words that the last segment of " - " splits often is, but which are NOT employers.
_TAIL_STOPLIST: set[str] = {
    "vp", "svp", "avp", "evp", "md", "associate", "director", "analyst",
    "vice president", "senior vice president", "managing director",
    "london", "new york", "singapore", "hong kong", "dublin", "edinburgh",
    "uk", "usa", "emea", "apac", "americas",
    "global", "remote", "hybrid", "contract", "permanent", "fte",
    "c++", "c#", "python", "java", "kdb", "rust", "go",
    "vice president - london", "director - london",
}


def _extract_real_employer(title: str, aggregator_label: str) -> str | None:
    """If `title` ends with ' - <Employer>' (common aggregator format), return <Employer>.

    Conservative: only fires when `aggregator_label` is one of the known aggregator
    brands (so we don't rewrite legitimate employer names).
    """
    if not title or " - " not in title:
        return None
    segments = [s.strip() for s in title.split(" - ") if s.strip()]
    if len(segments) < 2:
        return None
    last = segments[-1]
    lowered = last.lower()

    # Drop obviously non-employer tails
    if len(last) < 2 or len(last) > 60:
        return None
    if lowered in _TAIL_STOPLIST:
        # Fall back to second-to-last (e.g. "... - Citi - VP" → take "Citi")
        if len(segments) >= 3:
            last = segments[-2]
            lowered = last.lower()
            if lowered in _TAIL_STOPLIST or len(last) < 2:
                return None
        else:
            return None
    # Reject if last segment reads like a role
    if any(role_word in lowered for role_word in (
        "analyst", "manager", "engineer", "developer", "trader",
        "researcher", "strategist", "architect", "consultant",
    )):
        return None
    # Reject if same as aggregator label
    if lowered == aggregator_label.lower():
        return None
    return last


def _resolve_employer(record: dict[str, Any]) -> tuple[str, str | None]:
    """Return (display_company_name, extracted_real_employer_if_any).

    If the raw `company_name` looks like a known aggregator label, try to pull the
    real employer out of the title. Otherwise return the raw name unchanged.
    """
    raw = (record.get("company_name") or "").strip()
    if not raw:
        return "", None
    if raw.lower() in _AGGREGATOR_COMPANY_LABELS:
        real = _extract_real_employer(record.get("title") or "", raw)
        if real:
            return real, real
    return raw, None


def _parse_job(record: dict[str, Any], board_url: str, term: str) -> DiscoveredJobRecord | None:
    """Turn a Coresignal job record into a DiscoveredJobRecord."""
    job_id = record.get("id")
    title = (record.get("title") or "").strip() or None
    if not title:
        return None

    display_company, extracted_employer = _resolve_employer(record)
    company_name = display_company
    # Inject the resolved employer into the payload so the downstream aggregator
    # merge in server.py picks it up (it reads `employer_name` before `company_name`).
    if extracted_employer:
        # Don't mutate the original record shape — make a shallow copy
        record = dict(record)
        record["employer_name"] = extracted_employer
        record["_original_company_name"] = record.get("company_name")
        record["company_name"] = extracted_employer
    description = (record.get("description") or "").strip() or None
    posted_at = record.get("date_posted") or record.get("created") or record.get("updated_at")

    # Location — prefer city/state/country combination
    parts = [record.get(k) for k in ("city", "state", "country")]
    location = ", ".join(p for p in parts if p) or (record.get("location") or "").strip() or None

    # Apply URL — prefer the direct company site, fall back to source URL
    apply_url = (record.get("external_url") or "").strip()
    if not apply_url:
        job_sources = record.get("job_sources") or []
        if isinstance(job_sources, list) and job_sources:
            first_src = job_sources[0] if isinstance(job_sources[0], dict) else {}
            apply_url = (first_src.get("url") or "").strip()
    apply_url = apply_url or None

    completeness_score = sum(
        1 for v in [title, company_name, location, apply_url, description] if v
    ) / 5

    return DiscoveredJobRecord(
        external_job_id=str(job_id) if job_id is not None else apply_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=str(posted_at) if posted_at else None,
        summary_raw=description,
        discovered_url=apply_url,
        apply_url=apply_url,
        listing_payload=record,  # full record — critical for aggregator merge to find company_name
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.93,
        provenance={
            "adapter": "coresignal",
            "method": ExtractionMethod.API.value,
            "company": company_name,
            "platform": "Coresignal Multi-source",
            "search_term": term,
            "board_url": board_url,
        },
    )


def _extract_ids(search_resp: Any) -> list[int]:
    """Pull job IDs from a CoreSignal search response (handles list or ES hits shape)."""
    ids: list[int] = []
    if isinstance(search_resp, list):
        ids = [int(x) for x in search_resp if isinstance(x, (int, str)) and str(x).isdigit()]
    elif isinstance(search_resp, dict):
        hits = search_resp.get("hits", {}).get("hits", [])
        for h in hits:
            hid = h.get("_id") or h.get("_source", {}).get("id")
            if hid is None:
                continue
            try:
                ids.append(int(hid))
            except (TypeError, ValueError):
                continue
    return ids


def _extract_source_records(preview_resp: Any) -> list[dict[str, Any]]:
    """Extract job records from a CoreSignal preview response.

    Preview returns ES-style hits with `_source` inline, so /collect is unnecessary.
    Handles both ES-style hits and plain record lists defensively.
    """
    if isinstance(preview_resp, list):
        return [r for r in preview_resp if isinstance(r, dict)]
    if isinstance(preview_resp, dict):
        hits = preview_resp.get("hits", {}).get("hits", [])
        records: list[dict[str, Any]] = []
        for h in hits:
            if not isinstance(h, dict):
                continue
            src = h.get("_source")
            if not isinstance(src, dict):
                continue
            if "id" not in src and h.get("_id") is not None:
                src = dict(src)
                src["id"] = h["_id"]
            records.append(src)
        return records
    return []


class CoresignalAdapter(SourceAdapter):
    adapter_name = "coresignal"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=True,  # supports `since` via date_posted range
        supports_api=True,
        supports_html=False,
        supports_browser=False,
        supports_site_rescue=False,
        # Aggregator — see adzuna.py for rationale.
        complete_coverage_per_run=False,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        api_key = str(
            source_config.get("api_key")
            or os.getenv("CORESIGNAL_API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise ValueError(
                "CoresignalAdapter requires an API key — set CORESIGNAL_API_KEY env var "
                "or pass `api_key` in source_config"
            )

        # Company-filter mode: narrow every search to a single employer and
        # OR-union all taxonomy title phrases into ONE query per location (1 API call
        # per location instead of N_terms × N_locations). Enabled when config has
        # `company_filter` set — used by the "Add Company" UI flow.
        company_filter = str(source_config.get("company_filter") or "").strip() or None
        use_full_taxonomy = bool(source_config.get("use_full_taxonomy")) or company_filter is not None
        # When `max_per_category` is set, run one search per CATEGORY (not one union)
        # and cap collects per category — used for low-credit testing sweeps.
        max_per_category = source_config.get("max_per_category")
        max_per_category = int(max_per_category) if max_per_category is not None else None

        if use_full_taxonomy:
            search_terms = load_taxonomy_title_phrases()
        else:
            search_terms = [
                str(term).strip()
                for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS)
                if str(term).strip()
            ]
        # Location scope — each entry is {"country": ..., "city": ... (optional)}.
        # Defaults to UK + Germany + New York (city). Back-compat: if caller still
        # passes the old flat `countries` list we map it into location dicts.
        raw_locations = source_config.get("locations")
        if not raw_locations:
            legacy_countries = source_config.get("countries")
            if legacy_countries:
                raw_locations = [
                    {"country": str(c).strip()}
                    for c in legacy_countries
                    if str(c).strip()
                ]
        if not raw_locations:
            raw_locations = DEFAULT_LOCATIONS
        locations: list[dict[str, str]] = [
            loc for loc in raw_locations
            if isinstance(loc, dict) and loc.get("country")
        ]
        max_per_term = int(source_config.get("max_per_term", DEFAULT_MAX_PER_TERM))
        request_delay = float(source_config.get("request_delay", DEFAULT_REQUEST_DELAY))
        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        board_url = str(source_config.get("job_board_url") or "https://coresignal.com/").strip()

        # Freshness: only look at ads posted within the last `max_age_days` days
        # (by the advert's own post date, not Coresignal's scrape timestamp).
        max_age_days = int(source_config.get("max_age_days", DEFAULT_MAX_AGE_DAYS))
        freshness_cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        if since is None or since < freshness_cutoff:
            since = freshness_cutoff

        # Cost-control knobs (all default to the cheapest setting):
        # - `use_preview` (default True): hit /search/es_dsl/preview and parse `_source`
        #   inline, skipping /collect entirely. Preview records have fewer fields than
        #   collect — set False for full job detail at the cost of 1 credit per record.
        # - `union_terms` (default True): on the default path, OR-union all search terms
        #   into ONE query per location instead of one query per (term × location).
        #   Cuts search calls by N_terms (e.g. 24 → 3 for the stock taxonomy).
        # - `max_total_collects`: hard cap on records fetched across the entire run,
        #   applies to both collect and preview paths. Leave None for no cap.
        use_preview = bool(source_config.get("use_preview", True))
        union_terms = bool(source_config.get("union_terms", True))
        max_total_collects = source_config.get("max_total_collects")
        max_total_collects = int(max_total_collects) if max_total_collects is not None else None

        diagnostics = AdapterDiagnostics(
            metadata={
                "search_terms_count": len(search_terms),
                "locations": [_location_label(loc) for loc in locations],
                "max_per_term": max_per_term,
                "company_filter": company_filter,
                "use_full_taxonomy": use_full_taxonomy,
                "max_age_days": max_age_days,
                "since": since.isoformat(),
                "max_total_collects": max_total_collects,
                "use_preview": use_preview,
                "union_terms": union_terms,
            }
        )

        headers = {
            "accept": "application/json",
            "apikey": api_key,
            "Content-Type": "application/json",
        }

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()
        budget_exhausted = False

        async with httpx.AsyncClient(timeout=timeout_seconds, headers=headers) as client:
            if company_filter and max_per_category is not None:
                # ── Per-category path: one search per (location × category), cap collects ──
                taxonomy_by_cat = load_taxonomy_by_category()
                for location in locations:
                    if budget_exhausted:
                        break
                    loc_label = _location_label(location)
                    for category, cat_terms in taxonomy_by_cat.items():
                        if budget_exhausted:
                            break
                        query = _build_taxonomy_query(
                            company=company_filter,
                            location=location,
                            since=since,
                            title_phrases=cat_terms,
                        )
                        cat_key = f"cat_{category.lower().replace(' ', '_')}"
                        new_records, budget_exhausted = await self._fetch_records_for_query(
                            client=client,
                            query=query,
                            term_label=f"{category}:{loc_label}",
                            board_url=board_url,
                            diagnostics=diagnostics,
                            seen_ids=seen_ids,
                            max_ids=max_per_category,
                            request_delay=request_delay,
                            max_total_collects=max_total_collects,
                            use_preview=use_preview,
                            counter_prefix=cat_key,
                        )
                        all_records.extend(new_records)
                        diagnostics.counters[f"{cat_key}_records"] = len(new_records)

                        if on_page_scraped and new_records:
                            try:
                                page_num = diagnostics.counters.get("pages_reported", 0) + 1
                                diagnostics.counters["pages_reported"] = page_num
                                await on_page_scraped(page_num, new_records, all_records)
                            except Exception:
                                pass
            elif company_filter:
                # ── Company-scoped path: one union query per location ──
                for location in locations:
                    if budget_exhausted:
                        break
                    loc_label = _location_label(location)
                    query = _build_taxonomy_query(
                        company=company_filter,
                        location=location,
                        since=since,
                        title_phrases=search_terms,
                    )
                    new_records, budget_exhausted = await self._fetch_records_for_query(
                        client=client,
                        query=query,
                        term_label=f"taxonomy_sweep:{loc_label}",
                        board_url=board_url,
                        diagnostics=diagnostics,
                        seen_ids=seen_ids,
                        max_ids=max_per_term,
                        request_delay=request_delay,
                        max_total_collects=max_total_collects,
                        use_preview=use_preview,
                    )
                    all_records.extend(new_records)

                    if on_page_scraped and new_records:
                        try:
                            page_num = diagnostics.counters.get("pages_reported", 0) + 1
                            diagnostics.counters["pages_reported"] = page_num
                            await on_page_scraped(page_num, new_records, all_records)
                        except Exception:
                            pass
            elif union_terms:
                # ── Union path (default): one OR'd query per location, no company filter ──
                # Cuts search volume from N_terms × N_locations down to just N_locations.
                for location in locations:
                    if budget_exhausted:
                        break
                    loc_label = _location_label(location)
                    query = _build_taxonomy_query(
                        company=None,
                        location=location,
                        since=since,
                        title_phrases=search_terms,
                    )
                    new_records, budget_exhausted = await self._fetch_records_for_query(
                        client=client,
                        query=query,
                        term_label=f"union@{loc_label}",
                        board_url=board_url,
                        diagnostics=diagnostics,
                        seen_ids=seen_ids,
                        max_ids=max_per_term,
                        request_delay=request_delay,
                        max_total_collects=max_total_collects,
                        use_preview=use_preview,
                    )
                    all_records.extend(new_records)

                    if on_page_scraped and new_records:
                        try:
                            page_num = diagnostics.counters.get("pages_reported", 0) + 1
                            diagnostics.counters["pages_reported"] = page_num
                            await on_page_scraped(page_num, new_records, all_records)
                        except Exception:
                            pass
            else:
                # ── Legacy per-term path (opt-in via `union_terms: false`) ──
                for location in locations:
                    if budget_exhausted:
                        break
                    loc_label = _location_label(location)
                    for term in search_terms:
                        if budget_exhausted:
                            break
                        query = _build_es_query(term, location, since)
                        new_records, budget_exhausted = await self._fetch_records_for_query(
                            client=client,
                            query=query,
                            term_label=f"{term}@{loc_label}",
                            board_url=board_url,
                            diagnostics=diagnostics,
                            seen_ids=seen_ids,
                            max_ids=max_per_term,
                            request_delay=request_delay,
                            max_total_collects=max_total_collects,
                            use_preview=use_preview,
                        )
                        all_records.extend(new_records)

                        if on_page_scraped and new_records:
                            try:
                                page_num = diagnostics.counters.get("pages_reported", 0) + 1
                                diagnostics.counters["pages_reported"] = page_num
                                await on_page_scraped(page_num, new_records, all_records)
                            except Exception:
                                pass

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_ids"] = len(seen_ids)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _fetch_records_for_query(
        self,
        *,
        client: httpx.AsyncClient,
        query: dict,
        term_label: str,
        board_url: str,
        diagnostics: AdapterDiagnostics,
        seen_ids: set[str],
        max_ids: int,
        request_delay: float,
        max_total_collects: int | None,
        use_preview: bool,
        counter_prefix: str | None = None,
    ) -> tuple[list[DiscoveredJobRecord], bool]:
        """Run one search (or preview) query and return its parsed records.

        Centralises preview vs. search+collect branching and the shared
        `max_total_collects` budget, so each discovery path stays flat.

        Returns (new_records, budget_exhausted).
        """
        new_records: list[DiscoveredJobRecord] = []

        if use_preview:
            await asyncio.sleep(request_delay)
            preview_resp = await self._preview_with_retry(client, query, diagnostics)
            if preview_resp is None:
                return new_records, False
            diagnostics.counters["preview_requests"] = diagnostics.counters.get("preview_requests", 0) + 1

            source_records = _extract_source_records(preview_resp)[:max_ids]
            if counter_prefix:
                diagnostics.counters[f"{counter_prefix}_ids_seen"] = len(source_records)

            for record in source_records:
                if max_total_collects is not None and diagnostics.counters.get("records_fetched", 0) >= max_total_collects:
                    diagnostics.warnings.append(
                        f"coresignal: hit max_total_collects cap ({max_total_collects})"
                    )
                    return new_records, True
                key = str(record.get("id") or "")
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                diagnostics.counters["records_fetched"] = diagnostics.counters.get("records_fetched", 0) + 1
                parsed = _parse_job(record, board_url, term_label)
                if parsed:
                    new_records.append(parsed)
            return new_records, False

        # Search + collect path
        await asyncio.sleep(request_delay)
        search_resp = await self._search_with_retry(client, query, diagnostics)
        if search_resp is None:
            return new_records, False
        diagnostics.counters["search_requests"] = diagnostics.counters.get("search_requests", 0) + 1

        ids = _extract_ids(search_resp)[:max_ids]
        if counter_prefix:
            diagnostics.counters[f"{counter_prefix}_ids_seen"] = len(ids)
        if not ids:
            return new_records, False

        for job_id in ids:
            if max_total_collects is not None and diagnostics.counters.get("records_fetched", 0) >= max_total_collects:
                diagnostics.warnings.append(
                    f"coresignal: hit max_total_collects cap ({max_total_collects})"
                )
                return new_records, True
            key = str(job_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            await asyncio.sleep(request_delay)
            record = await self._collect_with_retry(client, job_id, diagnostics)
            if not record:
                continue
            diagnostics.counters["collect_requests"] = diagnostics.counters.get("collect_requests", 0) + 1
            diagnostics.counters["records_fetched"] = diagnostics.counters.get("records_fetched", 0) + 1
            parsed = _parse_job(record, board_url, term_label)
            if parsed:
                new_records.append(parsed)
        return new_records, False

    async def _search_with_retry(
        self,
        client: httpx.AsyncClient,
        query: dict,
        diagnostics: AdapterDiagnostics,
    ) -> Any | None:
        """POST ES DSL search, retry on transient errors."""
        _backoff = (5, 15, 30)
        _retryable = {429, 500, 502, 503, 504}
        for attempt in range(len(_backoff) + 1):
            try:
                resp = await client.post(SEARCH_ENDPOINT, json=query)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 402:
                    diagnostics.errors.append("Coresignal: out of credits (402)")
                    return None
                if resp.status_code == 401:
                    diagnostics.errors.append("Coresignal: invalid API key (401)")
                    return None
                if resp.status_code not in _retryable:
                    diagnostics.warnings.append(f"Search HTTP {resp.status_code}: {resp.text[:200]}")
                    return None
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
            except httpx.TimeoutException:
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
                else:
                    diagnostics.errors.append("Coresignal search: timeout")
                    return None
        return None

    async def _preview_with_retry(
        self,
        client: httpx.AsyncClient,
        query: dict,
        diagnostics: AdapterDiagnostics,
    ) -> Any | None:
        """POST ES DSL preview query, retry on transient errors.

        Mirrors `_search_with_retry` but hits /search/es_dsl/preview, which returns
        records with `_source` inline so no separate /collect call is needed.
        """
        _backoff = (5, 15, 30)
        _retryable = {429, 500, 502, 503, 504}
        for attempt in range(len(_backoff) + 1):
            try:
                resp = await client.post(PREVIEW_ENDPOINT, json=query)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 402:
                    diagnostics.errors.append("Coresignal: out of credits (402) during preview")
                    return None
                if resp.status_code == 401:
                    diagnostics.errors.append("Coresignal: invalid API key (401)")
                    return None
                if resp.status_code not in _retryable:
                    diagnostics.warnings.append(f"Preview HTTP {resp.status_code}: {resp.text[:200]}")
                    return None
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
            except httpx.TimeoutException:
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
                else:
                    diagnostics.errors.append("Coresignal preview: timeout")
                    return None
        return None

    async def _collect_with_retry(
        self,
        client: httpx.AsyncClient,
        job_id: int,
        diagnostics: AdapterDiagnostics,
    ) -> dict | None:
        """GET /collect/{id}, retry on transient errors."""
        _backoff = (5, 15, 30)
        _retryable = {429, 500, 502, 503, 504}
        for attempt in range(len(_backoff) + 1):
            try:
                resp = await client.get(f"{COLLECT_ENDPOINT}/{job_id}")
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None
                if resp.status_code == 402:
                    diagnostics.errors.append("Coresignal: out of credits (402) during collect")
                    return None
                if resp.status_code not in _retryable:
                    diagnostics.counters["collect_errors"] = diagnostics.counters.get("collect_errors", 0) + 1
                    return None
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
            except httpx.TimeoutException:
                if attempt < len(_backoff):
                    await asyncio.sleep(_backoff[attempt])
                else:
                    return None
        return None
