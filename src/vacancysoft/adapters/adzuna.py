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

API_BASE = "https://api.adzuna.com/v1/api/jobs"
RESULTS_PER_PAGE = 50
DEFAULT_MAX_PAGES = 5
DEFAULT_COUNTRIES = [
    ("gb", "UK"),
    ("us", "USA"),
    ("ca", "Canada"),
    ("de", "Germany"),
    ("fr", "France"),
    ("nl", "Netherlands"),
    ("ch", "Switzerland"),
    ("sg", "Singapore"),
]


def _format_salary(job: dict[str, Any]) -> str:
    lo = job.get("salary_min")
    hi = job.get("salary_max")
    currency = job.get("salary_currency") or "GBP"
    symbol = "£" if currency in {"GBP", "gbp"} else f"{currency} "
    if lo and hi and lo != hi:
        return f"{symbol}{int(lo):,} - {symbol}{int(hi):,}"
    if lo:
        return f"{symbol}{int(lo):,}+"
    return ""


def _normalise_country_config(country_values: list[str] | None) -> list[tuple[str, str]]:
    if not country_values:
        return DEFAULT_COUNTRIES
    normalised: list[tuple[str, str]] = []
    for value in country_values:
        code = str(value).strip().lower()
        if not code:
            continue
        label = next((name for existing_code, name in DEFAULT_COUNTRIES if existing_code == code), code.upper())
        normalised.append((code, label))
    return normalised or DEFAULT_COUNTRIES


def _parse_job(job: dict[str, Any], board_url: str) -> DiscoveredJobRecord:
    location_obj = job.get("location", {}) or {}
    area = location_obj.get("area", []) or []
    location = location_obj.get("display_name", "") or (area[-1] if area else "")
    discovered_url = job.get("redirect_url", "")
    completeness_score = sum(
        1 for value in [job.get("title"), location, discovered_url, job.get("created")] if value
    ) / 4

    return DiscoveredJobRecord(
        external_job_id=str(job.get("id") or discovered_url or job.get("title") or "").strip() or None,
        title_raw=str(job.get("title", "")).strip() or None,
        location_raw=location or None,
        posted_at_raw=str(job.get("created", "")).strip() or None,
        summary_raw=str(job.get("description", "")).strip() or None,
        discovered_url=discovered_url or None,
        apply_url=discovered_url or None,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "adzuna",
            "method": ExtractionMethod.API.value,
            "company": (job.get("company") or {}).get("display_name", ""),
            "salary": _format_salary(job),
            "contract_type": str(job.get("contract_time", "")).replace("_", " ").title(),
            "board_url": board_url,
        },
    )


class AdzunaAdapter(SourceAdapter):
    adapter_name = "adzuna"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=True,
        supports_html=False,
        supports_browser=False,
        supports_site_rescue=False,
        # Aggregator: per-run coverage is paginated by search params, not
        # the complete current set. MUST stay False — auto-mark-dead would
        # nuke jobs that just paginated past this run's window.
        complete_coverage_per_run=False,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        app_id = str(source_config.get("app_id") or os.getenv("ADZUNA_APP_ID") or "").strip()
        app_key = str(source_config.get("app_key") or os.getenv("ADZUNA_APP_KEY") or "").strip()
        if not app_id or not app_key:
            raise ValueError("Adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY")

        search_terms = [str(term).strip() for term in source_config.get("search_terms", []) if str(term).strip()]
        if not search_terms:
            raise ValueError("Adzuna source_config requires non-empty search_terms")

        # Optional company-scope filter — used by the Add Company preview
        # so we can ask Adzuna for jobs at a specific employer instead of
        # the standard taxonomy keyword sweep. Adzuna's `company` query
        # param is a loose substring match; downstream callers still
        # token-subset post-filter to weed out the false-positives.
        company_filter = (str(source_config.get("company_filter") or "")).strip()

        countries = _normalise_country_config(source_config.get("countries"))
        results_per_page = int(source_config.get("results_per_page", RESULTS_PER_PAGE))
        max_pages = int(source_config.get("max_pages", DEFAULT_MAX_PAGES))
        timeout_seconds = float(source_config.get("timeout_seconds", 20))

        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        diagnostics = AdapterDiagnostics(
            metadata={
                "search_terms": search_terms,
                "countries": [code for code, _ in countries],
                "results_per_page": results_per_page,
                "max_pages": max_pages,
            }
        )

        request_delay = float(source_config.get("request_delay", 1.0))

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for country_code, _country_name in countries:
                board_url = f"https://www.adzuna.com/{country_code}/search"
                for term in search_terms:
                    for page in range(1, max_pages + 1):
                        params = {
                            "app_id": app_id,
                            "app_key": app_key,
                            "what": term,
                            "results_per_page": results_per_page,
                        }
                        if company_filter:
                            params["company"] = company_filter
                        if since is not None:
                            params["max_days_old"] = max((datetime.utcnow().date() - since.date()).days, 1)

                        # Rate limiting — delay between requests to avoid 429
                        await asyncio.sleep(request_delay)

                        # Retry on transient errors (429, 500, 502, 503, 504)
                        _retryable = {429, 500, 502, 503, 504}
                        _backoff = (5, 15, 30)
                        response = None
                        for _attempt in range(len(_backoff) + 1):
                            try:
                                response = await client.get(f"{API_BASE}/{country_code}/search/{page}", params=params)
                                if response.status_code not in _retryable:
                                    break
                                if _attempt < len(_backoff):
                                    wait = _backoff[_attempt]
                                    diagnostics.warnings.append(f"HTTP {response.status_code} on {country_code}/{term}/p{page}, retry in {wait}s")
                                    await asyncio.sleep(wait)
                            except httpx.TimeoutException:
                                if _attempt < len(_backoff):
                                    await asyncio.sleep(_backoff[_attempt])
                                else:
                                    raise

                        response.raise_for_status()
                        data = response.json()
                        diagnostics.counters["http_requests"] = diagnostics.counters.get("http_requests", 0) + 1
                        results = data.get("results") or []
                        if not results:
                            break

                        records_before = len(all_records)
                        for job in results:
                            job_url = str(job.get("redirect_url") or "").strip()
                            if not job_url or job_url in seen_urls:
                                continue
                            seen_urls.add(job_url)
                            all_records.append(_parse_job(job, board_url))
                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                on_page_scraped(page, all_records[records_before:], all_records)
                            except Exception:
                                pass

                        total = int(data.get("count") or 0)
                        if page * results_per_page >= total:
                            break

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
