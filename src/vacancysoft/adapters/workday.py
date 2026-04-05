from __future__ import annotations

import re
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


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _extract_location(job: dict[str, Any]) -> str | None:
    locations = job.get("locationsText")
    if isinstance(locations, str) and locations.strip():
        return locations.strip()

    primary = job.get("primaryLocation")
    if isinstance(primary, str) and primary.strip():
        return primary.strip()

    locations_list = job.get("locations") or []
    if isinstance(locations_list, list) and locations_list:
        first = locations_list[0]
        if isinstance(first, dict):
            return _coalesce(first.get("displayName"), first.get("name"))
        if isinstance(first, str):
            return first.strip()
    return None


def _extract_summary(job: dict[str, Any]) -> str | None:
    return _clean_text(
        _coalesce(
            job.get("bulletFields"),
            job.get("jobDescription"),
            job.get("description"),
            job.get("shortDescription"),
        )
    )


def _extract_posted_at(job: dict[str, Any]) -> str | None:
    value = _coalesce(job.get("postedOn"), job.get("publicationDate"), job.get("postedDate"))
    if value is None:
        return None
    return str(value)


def _extract_apply_url(job: dict[str, Any], source_config: dict[str, Any]) -> str | None:
    direct = _coalesce(job.get("externalPath"), job.get("applyUrl"), job.get("jobUrl"))
    if isinstance(direct, str) and direct.startswith("http"):
        return direct

    base_url = str(source_config.get("job_board_url") or source_config.get("base_url") or "").rstrip("/")
    external_path = _coalesce(job.get("externalPath"), job.get("bulletFields"))
    if isinstance(external_path, str) and external_path.startswith("/") and base_url:
        return f"{base_url}{external_path}"

    req_id = _coalesce(job.get("bulletFields"), job.get("externalPath"), job.get("reqId"), job.get("jobReqId"))
    if base_url and isinstance(req_id, str):
        return f"{base_url}/job/{req_id}"
    return None


def _extract_discovered_url(job: dict[str, Any], source_config: dict[str, Any]) -> str | None:
    return _extract_apply_url(job, source_config)


def _extract_external_job_id(job: dict[str, Any]) -> str | None:
    return _coalesce(
        job.get("bulletFields"),
        job.get("externalPath"),
        job.get("reqId"),
        job.get("jobReqId"),
        job.get("id"),
        job.get("title"),
    )


def _job_to_record(job: dict[str, Any], source_config: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean_text(_coalesce(job.get("title"), job.get("jobTitle"), job.get("name")))
    location = _extract_location(job)
    posted_at = _extract_posted_at(job)
    summary = _extract_summary(job)
    discovered_url = _extract_discovered_url(job, source_config)
    apply_url = _extract_apply_url(job, source_config)

    completeness_fields = [title, location, posted_at, discovered_url]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=_extract_external_job_id(job),
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=discovered_url,
        apply_url=apply_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.9,
        provenance={
            "adapter": "workday",
            "method": ExtractionMethod.API.value,
        },
    )


def _extract_jobs(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("jobPostings", "jobPostingsList", "jobs", "positions"):
        value = response_json.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


class WorkdayAdapter(SourceAdapter):
    adapter_name = "workday"
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

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
    ) -> DiscoveryPage:
        endpoint_url = str(source_config.get("endpoint_url") or "").strip()
        if not endpoint_url:
            raise ValueError("Workday source_config requires endpoint_url")

        limit = int(source_config.get("limit", 20))
        offset = int(cursor or 0)
        payload: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "searchText": source_config.get("search_text", ""),
        }
        if since is not None:
            payload["postedAfter"] = since.date().isoformat()

        diagnostics = AdapterDiagnostics(metadata={"endpoint_url": endpoint_url, "offset": offset, "limit": limit})

        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            response = await client.post(endpoint_url, json=payload)
            response.raise_for_status()
            response_json = response.json()

        diagnostics.counters["status_code"] = response.status_code
        jobs = _extract_jobs(response_json)
        diagnostics.counters["jobs_seen"] = len(jobs)

        records = [_job_to_record(job, source_config) for job in jobs]
        next_cursor = str(offset + limit) if len(jobs) >= limit else None
        return DiscoveryPage(jobs=records, next_cursor=next_cursor, diagnostics=diagnostics)
