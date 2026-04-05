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


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _job_location(job: dict[str, Any]) -> str | None:
    location = _clean_text(((job.get("location") or {}).get("name") if isinstance(job.get("location"), dict) else None))
    if location:
        return location

    offices = job.get("offices") or []
    if isinstance(offices, list):
        for office in offices:
            if isinstance(office, dict):
                name = _clean_text(office.get("name"))
                if name:
                    return name
    return None


def _job_summary(job: dict[str, Any]) -> str | None:
    for key in ("content", "metadata", "internal_job_id"):
        value = job.get(key)
        if key == "metadata" and isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                meta_name = _clean_text(item.get("name"))
                meta_value = _clean_text(item.get("value"))
                if meta_name and meta_value:
                    parts.append(f"{meta_name}: {meta_value}")
                elif meta_value:
                    parts.append(meta_value)
            if parts:
                return " | ".join(parts)
        else:
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
    return None


def _parse_job(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    location = _job_location(job)
    discovered_url = _clean_text(job.get("absolute_url"))
    posted_at = _clean_text(job.get("updated_at"))
    title = _clean_text(job.get("title"))
    summary = _job_summary(job)
    external_job_id = _clean_text(job.get("id")) or discovered_url or title

    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=external_job_id,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.97,
        provenance={
            "adapter": "greenhouse",
            "method": ExtractionMethod.API.value,
            "company": str(board.get("company") or board.get("slug") or "").strip(),
            "platform": "Greenhouse",
            "board_url": str(board.get("url") or "").strip(),
            "office_count": len(job.get("offices") or []),
            "has_content": bool(_clean_text(job.get("content"))),
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
            "company": str(source_config.get("company") or slug).strip(),
            "url": str(source_config.get("job_board_url") or f"https://boards.greenhouse.io/{slug}").strip(),
        }
        url = f"{API_BASE}/{slug}/jobs"
        params = {"content": "true"}
        timeout_seconds = float(source_config.get("timeout_seconds", 20))
        diagnostics = AdapterDiagnostics(
            metadata={
                "slug": slug,
                "url": url,
                "job_board_url": board["url"],
                "since": since.isoformat() if since else None,
                "cursor_ignored": cursor is not None,
            }
        )
        if cursor is not None:
            diagnostics.warnings.append("GreenhouseAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append(
                "GreenhouseAdapter cannot enforce incremental sync at source. Results are filtered best-effort after fetch."
            )

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        jobs = [job for job in (data.get("jobs") or []) if isinstance(job, dict)]
        diagnostics.counters["status_code"] = int(response.status_code)
        diagnostics.counters["jobs_received"] = len(jobs)

        records: list[DiscoveredJobRecord] = []
        filtered_out = 0
        since_floor = since.date() if since else None
        for job in jobs:
            if since_floor is not None:
                raw_updated = _clean_text(job.get("updated_at"))
                if raw_updated:
                    try:
                        updated_dt = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                        if updated_dt.date() < since_floor:
                            filtered_out += 1
                            continue
                    except ValueError:
                        diagnostics.warnings.append(f"Unparseable updated_at value for Greenhouse job: {raw_updated}")
            records.append(_parse_job(job, board))

        diagnostics.counters["jobs_seen"] = len(records)
        diagnostics.counters["filtered_out_since"] = filtered_out
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
