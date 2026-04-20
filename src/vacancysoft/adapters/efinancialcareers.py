from __future__ import annotations

import asyncio
import time
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

# Public JSON API discovered from the eFinancialCareers SPA
API_BASE = "https://job-search-ui.efinancialcareers.com/v3/efc/jobs/search"

DEFAULT_SEARCH_TERMS = [
    "risk", "quant", "quantitative", "compliance", "strats", "pricing",
    "audit", "legal", "cyber", "trader", "trading",
]

# Country codes supported by the API
DEFAULT_COUNTRIES = [
    ("GB", "UK"),
    ("US", "USA"),
    ("CA", "Canada"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("NL", "Netherlands"),
    ("CH", "Switzerland"),
    ("SG", "Singapore"),
    ("HK", "Hong Kong"),
    ("IE", "Ireland"),
    ("AE", "UAE"),
]

PAGE_SIZE = 50
MAX_PAGES = 5
REQUEST_DELAY = 0.5


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_job(job: dict[str, Any], country_label: str) -> DiscoveredJobRecord:
    title = _clean(job.get("title"))
    company = _clean(job.get("companyName") or job.get("company") or job.get("advertiserName"))

    # Location
    location_parts = []
    loc_city = _clean(job.get("locationCity") or job.get("city"))
    loc_country = _clean(job.get("locationCountry") or job.get("country"))
    if loc_city:
        location_parts.append(loc_city)
    if loc_country:
        location_parts.append(loc_country)
    location = ", ".join(location_parts) if location_parts else country_label

    # URL
    details_path = _clean(job.get("detailsPageUrl"))
    if details_path and not details_path.startswith("http"):
        discovered_url = f"https://www.efinancialcareers.co.uk{details_path}"
    else:
        discovered_url = details_path

    posted_at = _clean(job.get("postedDate") or job.get("publishedDate") or job.get("createdAt") or job.get("firstPublishedDate"))

    # Salary
    salary = None
    salary_min = job.get("salaryMinimum") or job.get("salaryMin")
    salary_max = job.get("salaryMaximum") or job.get("salaryMax")
    salary_currency = _clean(job.get("salaryCurrency")) or ""
    if salary_min and salary_max:
        salary = f"{salary_currency}{int(salary_min):,} - {salary_currency}{int(salary_max):,}".strip()
    elif salary_min:
        salary = f"{salary_currency}{int(salary_min):,}+".strip()

    contract_type = _clean(job.get("employmentType") or job.get("contractType") or job.get("workType"))
    seniority = _clean(job.get("seniority"))

    summary_parts = [p for p in [salary, contract_type, seniority] if p]
    summary_raw = " | ".join(summary_parts) if summary_parts else None

    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=_clean(job.get("id")) or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.92,
        provenance={
            "adapter": "efinancialcareers",
            "method": ExtractionMethod.API.value,
            "company": company or "",
            "platform": "eFinancialCareers",
            "board_url": "https://www.efinancialcareers.co.uk",
            "salary": salary,
            "contract_type": contract_type,
        },
    )


class EFinancialCareersAdapter(SourceAdapter):
    adapter_name = "efinancialcareers"
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
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        search_terms = [
            str(t).strip() for t in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS)
            if str(t).strip()
        ]
        countries = source_config.get("countries") or DEFAULT_COUNTRIES
        # Normalise countries if passed as list of strings
        if countries and isinstance(countries[0], str):
            countries = [(c, c) for c in countries]

        page_size = int(source_config.get("page_size", PAGE_SIZE))
        max_pages = int(source_config.get("max_pages", MAX_PAGES))
        request_delay = float(source_config.get("request_delay", REQUEST_DELAY))
        timeout_seconds = float(source_config.get("timeout_seconds", 30))

        diagnostics = AdapterDiagnostics(
            metadata={"search_terms": search_terms, "countries": [c for c, _ in countries], "page_size": page_size}
        )
        if cursor is not None:
            diagnostics.warnings.append("EFinancialCareersAdapter does not support cursor-based pagination.")
        if since is not None:
            diagnostics.warnings.append("EFinancialCareersAdapter does not enforce incremental sync at source.")

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()
        t0 = time.monotonic()

        _retryable = {429, 500, 502, 503, 504}
        _backoff = (5, 15, 30)

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for country_code, country_label in countries:
                for term in search_terms:
                    for page_num in range(1, max_pages + 1):
                        await asyncio.sleep(request_delay)

                        params = {
                            "q": term,
                            "countryCode2": country_code,
                            "radius": 40,
                            "radiusUnit": "km",
                            "page": page_num,
                            "pageSize": page_size,
                            "facets": "seniority|fullTime|functionCode|industryCode|cityCode",
                        }

                        # Retry on transient errors
                        response = None
                        for attempt in range(len(_backoff) + 1):
                            try:
                                response = await client.get(API_BASE, params=params)
                                if response.status_code not in _retryable:
                                    break
                                if attempt < len(_backoff):
                                    diagnostics.warnings.append(
                                        f"HTTP {response.status_code} on {country_code}/{term}/p{page_num}, retry in {_backoff[attempt]}s"
                                    )
                                    await asyncio.sleep(_backoff[attempt])
                            except httpx.TimeoutException:
                                if attempt < len(_backoff):
                                    await asyncio.sleep(_backoff[attempt])
                                else:
                                    raise

                        if response is None or response.status_code >= 400:
                            diagnostics.counters["api_errors"] = diagnostics.counters.get("api_errors", 0) + 1
                            break

                        diagnostics.counters["http_requests"] = diagnostics.counters.get("http_requests", 0) + 1

                        data = response.json()
                        jobs = data.get("data") or []
                        if not jobs:
                            break

                        records_before = len(all_records)
                        for job in jobs:
                            if not isinstance(job, dict):
                                continue
                            job_id = _clean(job.get("id"))
                            if job_id and job_id in seen_ids:
                                diagnostics.counters["duplicates"] = diagnostics.counters.get("duplicates", 0) + 1
                                continue
                            if job_id:
                                seen_ids.add(job_id)
                            all_records.append(_parse_job(job, country_label))

                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                on_page_scraped(page_num, all_records[records_before:], all_records)
                            except Exception:
                                pass

                        total_count = data.get("totalCount") or data.get("total") or 0
                        if page_num * page_size >= total_count:
                            break

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_ids"] = len(seen_ids)
        diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)

        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
