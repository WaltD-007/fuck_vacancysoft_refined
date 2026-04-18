from __future__ import annotations

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

API_BASE = "https://www.reed.co.uk/api/1.0/search"
BOARD_URL = "https://www.reed.co.uk"
RESULTS_PER_PAGE = 100
MAX_SKIP = 400
DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]
DEFAULT_LOCATIONS = ["London", "Manchester", "Birmingham", "Edinburgh", "Glasgow", "Bristol", "Leeds", "Dublin"]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_salary(job: dict[str, Any]) -> str | None:
    low = job.get("minimumSalary")
    high = job.get("maximumSalary")
    if low and high and low != high:
        return f"£{int(low):,} - £{int(high):,}"
    if low:
        return f"£{int(low):,}+"
    return None


def _parse_job(job: dict[str, Any]) -> DiscoveredJobRecord:
    job_id = _clean(job.get("jobId"))
    salary = _format_salary(job)
    contract_type = _clean(job.get("contractType"))
    summary_raw = " | ".join(part for part in [salary, contract_type] if part) or None
    title = _clean(job.get("jobTitle"))
    location = _clean(job.get("locationName"))
    discovered_url = f"https://www.reed.co.uk/jobs/{job_id}" if job_id else None
    completeness_fields = [title, location, discovered_url, _clean(job.get("date"))]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=job_id or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=_clean(job.get("date")),
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "reed",
            "method": ExtractionMethod.API.value,
            "company": _clean(job.get("employerName")) or "",
            "platform": "Reed",
            "board_url": BOARD_URL,
            "salary": salary,
            "contract_type": contract_type,
        },
    )


class ReedAdapter(SourceAdapter):
    adapter_name = "reed"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=True,
        supports_incremental_sync=False,
        supports_api=True,
        supports_html=False,
        supports_browser=False,
        supports_site_rescue=False,
    )

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        api_key = _clean(source_config.get("reed_api_key")) or _clean(os.getenv("REED_API_KEY"))
        diagnostics = AdapterDiagnostics(metadata={"api_base": API_BASE})
        if not api_key:
            diagnostics.warnings.append("REED_API_KEY not set. Reed discovery skipped.")
            return DiscoveryPage(jobs=[], next_cursor=None, diagnostics=diagnostics)
        terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        locations = [str(loc).strip() for loc in (source_config.get("locations") or DEFAULT_LOCATIONS) if str(loc).strip()]
        if cursor:
            location_index, term_index, skip = [int(part) for part in cursor.split(":", 2)]
        else:
            location_index, term_index, skip = 0, 0, 0
        auth = httpx.BasicAuth(api_key, "")
        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()
        next_cursor: str | None = None
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20)), auth=auth) as client:
            for li in range(location_index, len(locations)):
                location = locations[li]
                start_ti = term_index if li == location_index else 0
                for ti in range(start_ti, len(terms)):
                    term = terms[ti]
                    current_skip = skip if (li == location_index and ti == term_index) else 0
                    while True:
                        response = await client.get(API_BASE, params={"keywords": term, "locationName": location, "distanceFromLocation": 15, "resultsToTake": RESULTS_PER_PAGE, "resultsToSkip": current_skip})
                        response.raise_for_status()
                        data = response.json()
                        diagnostics.counters["status_code"] = int(response.status_code)
                        diagnostics.counters["requests_made"] = diagnostics.counters.get("requests_made", 0) + 1
                        results = data.get("results") or []
                        if not results:
                            break
                        for job in results:
                            if not isinstance(job, dict):
                                continue
                            job_id = _clean(job.get("jobId"))
                            if job_id and job_id in seen_ids:
                                diagnostics.counters["duplicates"] = diagnostics.counters.get("duplicates", 0) + 1
                                continue
                            if job_id:
                                seen_ids.add(job_id)
                            all_records.append(_parse_job(job))
                        if len(results) < RESULTS_PER_PAGE:
                            break
                        current_skip += RESULTS_PER_PAGE
                        if current_skip >= MAX_SKIP:
                            break
                        next_cursor = f"{li}:{ti}:{current_skip}"
                    skip = 0
                term_index = 0
        diagnostics.counters["jobs_seen"] = len(all_records)
        return DiscoveryPage(jobs=all_records, next_cursor=next_cursor, diagnostics=diagnostics)
