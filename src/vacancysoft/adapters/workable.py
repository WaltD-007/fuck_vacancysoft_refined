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

WIDGET_BASE = "https://apply.workable.com/api/v1/widget/accounts"


def _job_location(job: dict[str, Any]) -> str:
    city = str(job.get("city") or "").strip()
    country = str(job.get("country") or "").strip()
    parts = [p for p in [city, country] if p]
    location = ", ".join(parts)
    if location:
        return location

    locs = job.get("locations") or []
    if locs and isinstance(locs[0], dict):
        loc0 = locs[0]
        return str(loc0.get("city") or loc0.get("country") or "").strip()
    return ""


def _job_url(slug: str, shortcode: str | None) -> str:
    short = str(shortcode or "").strip()
    return f"https://apply.workable.com/{slug}/j/{short}" if short else f"https://apply.workable.com/{slug}"


def _parse_job(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    slug = str(board.get("slug") or "").strip()
    location = _job_location(job)
    discovered_url = _job_url(slug, job.get("shortcode"))
    completeness_score = sum(
        1 for value in [job.get("title"), location, discovered_url, job.get("published_on")] if value
    ) / 4

    return DiscoveredJobRecord(
        external_job_id=str(job.get("id") or job.get("shortcode") or discovered_url).strip() or None,
        title_raw=str(job.get("title") or "").strip() or None,
        location_raw=location or None,
        posted_at_raw=str(job.get("published_on") or "").strip() or None,
        summary_raw=str(job.get("description") or job.get("requirements") or "").strip() or None,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "workable",
            "method": ExtractionMethod.API.value,
            "company": str(board.get("company") or slug),
            "platform": "Workable",
            "board_url": str(board.get("url") or f"https://apply.workable.com/{slug}"),
            "contract_type": str(job.get("employment_type") or "").strip(),
        },
    )


class WorkableAdapter(SourceAdapter):
    adapter_name = "workable"
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
            raise ValueError("Workable source_config requires slug")

        board = {
            "slug": slug,
            "company": str(source_config.get("company") or slug),
            "url": str(source_config.get("job_board_url") or f"https://apply.workable.com/{slug}"),
        }
        url = f"{WIDGET_BASE}/{slug}"
        timeout_seconds = float(source_config.get("timeout_seconds", 20))
        diagnostics = AdapterDiagnostics(metadata={"slug": slug, "url": url})

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        diagnostics.counters["status_code"] = response.status_code
        jobs = data.get("jobs") or []
        records = [_parse_job(job, board) for job in jobs if isinstance(job, dict)]
        diagnostics.counters["jobs_seen"] = len(records)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
