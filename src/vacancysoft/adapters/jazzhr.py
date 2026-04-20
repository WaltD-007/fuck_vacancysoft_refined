from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin

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
from vacancysoft.source_registry.legacy_board_mappings import lookup_company


API_PATHS = (
    "/jobs",
    "/jobs/json",
    "/feed/json",
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_job(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean(job.get("title") or job.get("name") or job.get("job_title"))
    city = _clean(job.get("city"))
    state = _clean(job.get("state"))
    country = _clean(job.get("country"))
    location = ", ".join(part for part in [city, state, country] if part) or _clean(job.get("location"))
    discovered_url = _clean(job.get("url") or job.get("absolute_url") or job.get("apply_url"))
    if discovered_url and not discovered_url.startswith("http"):
        discovered_url = urljoin(str(board.get("url") or ""), discovered_url)
    posted_at = _clean(job.get("created") or job.get("created_at") or job.get("updated_at"))
    summary = " | ".join(
        part for part in [_clean(job.get("department")), _clean(job.get("employment_type")), _clean(job.get("type"))] if part
    ) or None
    company_name = lookup_company("jazzhr", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=_clean(job.get("id")) or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.92,
        provenance={
            "adapter": "jazzhr",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "JazzHR",
            "board_url": str(board.get("url") or ""),
        },
    )


class JazzHRAdapter(SourceAdapter):
    adapter_name = "jazzhr"
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

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").rstrip("/")
        if not board_url:
            raise ValueError("JazzHRAdapter requires job_board_url")
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        if cursor is not None:
            diagnostics.warnings.append("JazzHRAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("JazzHRAdapter does not enforce incremental sync at source. since was ignored.")
        payload = None
        tried: list[str] = []
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            for path in API_PATHS:
                candidate = f"{board_url}{path}"
                tried.append(candidate)
                response = await client.get(candidate)
                content_type = response.headers.get("content-type", "")
                if response.status_code == 200 and "json" in content_type.lower():
                    payload = response.json()
                    diagnostics.metadata["feed_url"] = candidate
                    diagnostics.counters["status_code"] = int(response.status_code)
                    break
        if payload is None:
            diagnostics.errors.append("No JazzHR public JSON feed responded successfully.")
            diagnostics.metadata["tried_feed_urls"] = tried
            return DiscoveryPage(jobs=[], next_cursor=None, diagnostics=diagnostics)
        jobs = payload.get("jobs") or payload.get("data") or payload if isinstance(payload, list) else []
        board = {"url": board_url, "company": source_config.get("company")}
        records = [_parse_job(job, board) for job in jobs if isinstance(job, dict)]
        diagnostics.counters["jobs_seen"] = len(records)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
