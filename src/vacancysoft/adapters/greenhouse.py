from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    SourceAdapter,
)

API_BASE = "https://api.greenhouse.io/v1/boards"


def _job_location(job: dict[str, Any]) -> str:
    offices = job.get("offices") or []
    location = ((job.get("location") or {}).get("name") or "").strip()
    if not location and offices:
        first = offices[0] or {}
        location = str(first.get("name") or "").strip()
    return location


def _parse_job(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    location = _job_location(job)
    discovered_url = str(job.get("absolute_url") or "").strip()
    completeness_score = sum(
        1 for value in [job.get("title"), location, discovered_url, job.get("updated_at")] if value
    ) / 4
    return DiscoveredJobRecord(
        external_job_id=str(job.get("id") or discovered_url or job.get("title") or "").strip() or None,
        title_raw=str(job.get("title") or "").strip() or None,
        location_raw=location or None,
        posted_at_raw=str(job.get("updated_at") or "").strip() or None,
        summary_raw=str(job.get("content") or "").strip() or None,
        discovered_url=discovered_url or None,
        apply_url=discovered_url or None,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.97,
        provenance={
            "adapter": "greenhouse",
            "method": ExtractionMethod.API.value,
            "company": str(board.get("company") or board.get("slug") or ""),
            "platform": "Greenhouse",
            "board_url": str(board.get("url") or ""),
        },
    )


class GreenhouseAdapter(SourceAdapter):
    adapter_name = "greenhouse"
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
    ) -> DiscoveryPage:
        slug = str(source_config.get("slug") or "").strip()
        if not slug:
            raise ValueError("Greenhouse source_config requires slug")

        board = {
            "slug": slug,
            "company": str(source_config.get("company") or slug),
            "url": str(source_config.get("job_board_url") or f"https://boards.greenhouse.io/{slug}"),
        }
        url = f"{API_BASE}/{slug}/jobs"
        params = {"content": "true"}
        timeout_seconds = float(source_config.get("timeout_seconds", 20))
        diagnostics = AdapterDiagnostics(metadata={"slug": slug, "url": url})

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        jobs = data.get("jobs") or []
        records = [_parse_job(job, board) for job in jobs if isinstance(job, dict)]
        diagnostics.counters["status_code"] = response.status_code
        diagnostics.counters["jobs_seen"] = len(records)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
