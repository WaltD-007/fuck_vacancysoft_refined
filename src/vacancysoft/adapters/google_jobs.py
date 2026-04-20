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

SERPAPI_URL = "https://serpapi.com/search"
BOARD_URL = "https://jobs.google.com"
DEFAULT_LOCATIONS = [
    ("London, United Kingdom", "UK"),
    ("New York, NY, United States", "USA"),
    ("Toronto, Ontario, Canada", "Canada"),
    ("Frankfurt, Germany", "Germany"),
    ("Paris, France", "France"),
    ("Amsterdam, Netherlands", "Netherlands"),
    ("Dublin, Ireland", "Ireland"),
    ("Zurich, Switzerland", "Switzerland"),
    ("Dubai, United Arab Emirates", "UAE"),
    ("Riyadh, Saudi Arabia", "Saudi Arabia"),
    ("Hong Kong", "Hong Kong"),
    ("Singapore", "Singapore"),
]
DEFAULT_QUERIES = [
    "risk manager finance",
    "credit risk analyst",
    "market risk",
    "quantitative analyst finance",
    "compliance officer financial services",
    "audit manager financial services",
    "cyber security financial services",
    "trader fixed income",
    "portfolio manager",
]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_locations(raw_locations: Any) -> list[tuple[str, str]]:
    if not raw_locations:
        return list(DEFAULT_LOCATIONS)
    normalised: list[tuple[str, str]] = []
    for item in raw_locations:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            location = _clean(item[0])
            country = _clean(item[1])
            if location and country:
                normalised.append((location, country))
        elif isinstance(item, dict):
            location = _clean(item.get("location"))
            country = _clean(item.get("country"))
            if location and country:
                normalised.append((location, country))
    return normalised or list(DEFAULT_LOCATIONS)


def _pick_job_url(result: dict[str, Any]) -> str | None:
    apply_options = result.get("apply_options") or []
    if isinstance(apply_options, list):
        for option in apply_options:
            if not isinstance(option, dict):
                continue
            link = _clean(option.get("link"))
            if link and "google.com/aclk" not in link:
                return link
    return _clean(result.get("job_link")) or _clean(result.get("share_link"))


def _job_summary(result: dict[str, Any]) -> str | None:
    parts: list[str] = []
    description = _clean(result.get("description"))
    if description:
        parts.append(description)
    extensions = result.get("detected_extensions") or {}
    if isinstance(extensions, dict):
        for key in ("posted_at", "schedule_type", "salary"):
            value = _clean(extensions.get(key))
            if value:
                parts.append(f"{key}: {value}")
    return " | ".join(parts) if parts else None


def _parse_result(result: dict[str, Any], query: str, location_seed: str, country_seed: str) -> DiscoveredJobRecord | None:
    title = _clean(result.get("title"))
    if not title:
        return None

    company = _clean(result.get("company_name"))
    location_raw = _clean(result.get("location")) or location_seed
    job_id = _clean(result.get("job_id"))
    job_url = _pick_job_url(result)
    extensions = result.get("detected_extensions") or {}
    posted_at = _clean(extensions.get("posted_at")) if isinstance(extensions, dict) else None
    salary = _clean(extensions.get("salary")) if isinstance(extensions, dict) else None
    schedule_type = _clean(extensions.get("schedule_type")) if isinstance(extensions, dict) else None
    summary = _job_summary(result)
    external_job_id = job_id or job_url or title
    completeness_fields = [title, company, location_raw, job_url]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=external_job_id,
        title_raw=title,
        location_raw=location_raw,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=job_url,
        apply_url=job_url,
        listing_payload=result,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.84,
        provenance={
            "adapter": "google_jobs",
            "method": ExtractionMethod.API.value,
            "company": company or "",
            "platform": "Google Jobs",
            "board_url": BOARD_URL,
            "query": query,
            "location_seed": location_seed,
            "country_seed": country_seed,
            "salary": salary,
            "schedule_type": schedule_type,
        },
    )


class GoogleJobsAdapter(SourceAdapter):
    adapter_name = "google_jobs"
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
        api_key = _clean(source_config.get("serpapi_api_key")) or _clean(os.getenv("SERPAPI_KEY"))
        queries = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_QUERIES) if str(term).strip()]
        locations = _normalise_locations(source_config.get("locations"))
        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        serpapi_url = str(source_config.get("serpapi_url") or SERPAPI_URL).strip()
        hl = str(source_config.get("hl") or "en").strip()
        gl = str(source_config.get("gl") or "us").strip()
        max_pages_per_query = max(1, min(int(source_config.get("max_pages_per_query", 2)), 5))
        diagnostics = AdapterDiagnostics(
            metadata={
                "serpapi_url": serpapi_url,
                "query_count": len(queries),
                "location_count": len(locations),
                "hl": hl,
                "gl": gl,
                "max_pages_per_query": max_pages_per_query,
                "since": since.isoformat() if since else None,
                "cursor_ignored": cursor is not None,
            }
        )
        if cursor is not None:
            diagnostics.warnings.append("GoogleJobsAdapter does not support pagination cursors. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append(
                "GoogleJobsAdapter cannot reliably enforce incremental sync because Google Jobs posted_at values are often relative or unstructured."
            )
        if not api_key:
            diagnostics.warnings.append("SERPAPI_KEY not set. Google Jobs discovery skipped.")
            return DiscoveryPage(jobs=[], next_cursor=None, diagnostics=diagnostics)

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()
        quota_exhausted = False

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for query in queries:
                if quota_exhausted:
                    break
                for location, country in locations:
                    if quota_exhausted:
                        break
                    next_page_token: str | None = None
                    for page_index in range(max_pages_per_query):
                        params = {
                            "engine": "google_jobs",
                            "q": query,
                            "location": location,
                            "api_key": api_key,
                            "hl": hl,
                            "gl": gl,
                        }
                        if next_page_token:
                            params["next_page_token"] = next_page_token
                        response = await client.get(serpapi_url, params=params)
                        diagnostics.counters["requests_made"] = diagnostics.counters.get("requests_made", 0) + 1
                        if response.status_code in {401, 403, 429}:
                            diagnostics.errors.append(
                                f"SerpApi returned status {response.status_code}. Stopping Google Jobs discovery."
                            )
                            quota_exhausted = True
                            break
                        response.raise_for_status()
                        data = response.json()
                        jobs = data.get("jobs_results") or []
                        if not jobs:
                            if page_index == 0:
                                diagnostics.warnings.append(
                                    f"No Google Jobs results returned for query='{query}' location='{location}'."
                                )
                            break
                        diagnostics.counters["jobs_received"] = diagnostics.counters.get("jobs_received", 0) + len(jobs)
                        records_before = len(all_records)
                        for job in jobs:
                            if not isinstance(job, dict):
                                continue
                            job_id = _clean(job.get("job_id")) or _pick_job_url(job)
                            if job_id and job_id in seen_ids:
                                diagnostics.counters["duplicate_jobs"] = diagnostics.counters.get("duplicate_jobs", 0) + 1
                                continue
                            if job_id:
                                seen_ids.add(job_id)
                            record = _parse_result(job, query=query, location_seed=location, country_seed=country)
                            if record:
                                all_records.append(record)
                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                on_page_scraped(page_index + 1, all_records[records_before:], all_records)
                            except Exception:
                                pass
                        next_page_token = _clean(((data.get("serpapi_pagination") or {}).get("next_page_token")))
                        if not next_page_token:
                            break

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_ids"] = len(seen_ids)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
