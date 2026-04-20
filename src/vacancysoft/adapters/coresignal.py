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
from datetime import datetime
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
DEFAULT_COUNTRIES = ["United Kingdom"]
DEFAULT_MAX_PER_TERM = 100  # max IDs retrieved per search term/country combo
DEFAULT_REQUEST_DELAY = 0.1  # 18 req/sec cap on search endpoint


def _build_es_query(term: str, country: str, since: datetime | None) -> dict:
    """Build an ElasticSearch DSL query filtering by title keywords + country + date.

    Notes on field quirks:
      * `country` needs `match_phrase` to avoid token-match leaks (e.g. "United Kingdom"
        matching US jobs whose location contains "United" or "Kingdom").
      * Coresignal date filter field is `created_at` with `yyyy-MM-dd HH:mm:ss` format.
    """
    must: list[dict] = [
        {"match_phrase": {"title": term}} if " " in term else {"match": {"title": term}},
        {"match_phrase": {"country": country}},
    ]
    if since is not None:
        must.append({
            "range": {
                "created_at": {"gte": since.strftime("%Y-%m-%d %H:%M:%S")}
            }
        })
    return {"query": {"bool": {"must": must}}}


def _build_taxonomy_query(
    *,
    company: str | None,
    country: str,
    since: datetime | None,
    title_phrases: list[str],
) -> dict:
    """Build an ES DSL query matching ANY of the taxonomy title phrases for a
    specific company, country, and date window.

    Used by the Add Company flow to sweep the full taxonomy against one employer.
    """
    shoulds = [
        {"match_phrase": {"title": t}} if " " in t else {"match": {"title": t}}
        for t in title_phrases
    ]
    must: list[dict] = [
        {"match_phrase": {"country": country}},
    ]
    if company:
        must.append({"match_phrase": {"company_name": company}})
    if since is not None:
        must.append({
            "range": {"created_at": {"gte": since.strftime("%Y-%m-%d %H:%M:%S")}}
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
        # OR-union all taxonomy title phrases into ONE query per country (1 API call
        # per country instead of N_terms × N_countries). Enabled when config has
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
        countries = [
            str(c).strip()
            for c in (source_config.get("countries") or DEFAULT_COUNTRIES)
            if str(c).strip()
        ]
        max_per_term = int(source_config.get("max_per_term", DEFAULT_MAX_PER_TERM))
        request_delay = float(source_config.get("request_delay", DEFAULT_REQUEST_DELAY))
        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        board_url = str(source_config.get("job_board_url") or "https://coresignal.com/").strip()

        diagnostics = AdapterDiagnostics(
            metadata={
                "search_terms_count": len(search_terms),
                "countries": countries,
                "max_per_term": max_per_term,
                "company_filter": company_filter,
                "use_full_taxonomy": use_full_taxonomy,
            }
        )

        headers = {
            "accept": "application/json",
            "apikey": api_key,
            "Content-Type": "application/json",
        }

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=timeout_seconds, headers=headers) as client:
            if company_filter and max_per_category is not None:
                # ── Per-category path: one search per (country × category), cap collects ──
                taxonomy_by_cat = load_taxonomy_by_category()
                for country in countries:
                    for category, cat_terms in taxonomy_by_cat.items():
                        query = _build_taxonomy_query(
                            company=company_filter,
                            country=country,
                            since=since,
                            title_phrases=cat_terms,
                        )
                        await asyncio.sleep(request_delay)
                        search_resp = await self._search_with_retry(client, query, diagnostics)
                        if search_resp is None:
                            continue
                        diagnostics.counters["search_requests"] = diagnostics.counters.get("search_requests", 0) + 1
                        cat_key = f"cat_{category.lower().replace(' ', '_')}"

                        ids: list[int] = []
                        if isinstance(search_resp, list):
                            ids = [int(x) for x in search_resp if isinstance(x, (int, str)) and str(x).isdigit()]
                        diagnostics.counters[f"{cat_key}_ids_seen"] = len(ids)
                        if not ids:
                            continue

                        # Hard cap per category for credit safety
                        ids = ids[:max_per_category]

                        records_before = len(all_records)
                        for job_id in ids:
                            key = str(job_id)
                            if key in seen_ids:
                                continue
                            seen_ids.add(key)
                            await asyncio.sleep(request_delay)
                            record = await self._collect_with_retry(client, job_id, diagnostics)
                            if not record:
                                continue
                            diagnostics.counters["collect_requests"] = diagnostics.counters.get("collect_requests", 0) + 1
                            parsed = _parse_job(record, board_url, term=f"{category}:{country}")
                            if parsed:
                                all_records.append(parsed)
                        diagnostics.counters[f"{cat_key}_records"] = len(all_records) - records_before

                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                page_num = diagnostics.counters.get("pages_reported", 0) + 1
                                diagnostics.counters["pages_reported"] = page_num
                                await on_page_scraped(page_num, all_records[records_before:], all_records)
                            except Exception:
                                pass
            elif company_filter:
                # ── Company-scoped path: one union query per country ──
                for country in countries:
                    query = _build_taxonomy_query(
                        company=company_filter,
                        country=country,
                        since=since,
                        title_phrases=search_terms,
                    )
                    await asyncio.sleep(request_delay)
                    search_resp = await self._search_with_retry(client, query, diagnostics)
                    if search_resp is None:
                        continue
                    diagnostics.counters["search_requests"] = diagnostics.counters.get("search_requests", 0) + 1

                    ids: list[int] = []
                    if isinstance(search_resp, list):
                        ids = [int(x) for x in search_resp if isinstance(x, (int, str)) and str(x).isdigit()]
                    elif isinstance(search_resp, dict):
                        hits = search_resp.get("hits", {}).get("hits", [])
                        for h in hits:
                            hid = h.get("_id") or h.get("_source", {}).get("id")
                            if hid is not None:
                                ids.append(int(hid))

                    ids = ids[:max_per_term]
                    if not ids:
                        continue

                    records_before = len(all_records)
                    for job_id in ids:
                        key = str(job_id)
                        if key in seen_ids:
                            continue
                        seen_ids.add(key)
                        await asyncio.sleep(request_delay)
                        record = await self._collect_with_retry(client, job_id, diagnostics)
                        if not record:
                            continue
                        diagnostics.counters["collect_requests"] = diagnostics.counters.get("collect_requests", 0) + 1
                        parsed = _parse_job(record, board_url, term=f"taxonomy_sweep:{country}")
                        if parsed:
                            all_records.append(parsed)

                    if on_page_scraped and len(all_records) > records_before:
                        try:
                            page_num = diagnostics.counters.get("pages_reported", 0) + 1
                            diagnostics.counters["pages_reported"] = page_num
                            await on_page_scraped(page_num, all_records[records_before:], all_records)
                        except Exception:
                            pass
            else:
                # ── Original per-term path ──
                for country in countries:
                    for term in search_terms:
                        query = _build_es_query(term, country, since)

                        # 1. Search — returns list of job IDs (and optionally _source hits)
                        await asyncio.sleep(request_delay)
                        search_resp = await self._search_with_retry(client, query, diagnostics)
                        if search_resp is None:
                            continue
                        diagnostics.counters["search_requests"] = diagnostics.counters.get("search_requests", 0) + 1

                        # Coresignal search returns an array of IDs (ints), not ES-style hits
                        ids: list[int] = []
                        if isinstance(search_resp, list):
                            ids = [int(x) for x in search_resp if isinstance(x, (int, str)) and str(x).isdigit()]
                        elif isinstance(search_resp, dict):
                            # Fall back to ES hits shape if that's what's returned
                            hits = search_resp.get("hits", {}).get("hits", [])
                            for h in hits:
                                hid = h.get("_id") or h.get("_source", {}).get("id")
                                if hid is not None:
                                    ids.append(int(hid))

                        ids = ids[:max_per_term]
                        if not ids:
                            continue

                        # 2. Collect — fetch full record for each ID
                        records_before = len(all_records)
                        for job_id in ids:
                            key = str(job_id)
                            if key in seen_ids:
                                continue
                            seen_ids.add(key)

                            await asyncio.sleep(request_delay)
                            record = await self._collect_with_retry(client, job_id, diagnostics)
                            if not record:
                                continue
                            diagnostics.counters["collect_requests"] = diagnostics.counters.get("collect_requests", 0) + 1

                            parsed = _parse_job(record, board_url, term)
                            if parsed:
                                all_records.append(parsed)

                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                page_num = diagnostics.counters.get("pages_reported", 0) + 1
                                diagnostics.counters["pages_reported"] = page_num
                                await on_page_scraped(page_num, all_records[records_before:], all_records)
                            except Exception:
                                pass

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_ids"] = len(seen_ids)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)

    # ── Helpers ──────────────────────────────────────────────────────────

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
