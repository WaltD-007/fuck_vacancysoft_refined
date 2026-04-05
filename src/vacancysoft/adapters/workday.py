from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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
_GENERIC_COMPANY_VALUES = {"jobs", "careers", "workday", "candidateexperience", "hcmui"}


def derive_workday_candidate_endpoints(job_board_url: str) -> list[str]:
    parsed = urlparse(job_board_url)
    host = parsed.netloc
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return []

    locale = parts[0]
    site = parts[1]

    tenant_candidates: list[str] = []
    subdomain = host.split(".")[0]
    if subdomain and subdomain not in tenant_candidates:
        tenant_candidates.append(subdomain)
    for candidate in (site, site.replace("-", "_"), site.replace("-", ""), site.replace("_", "-")):
        if candidate and candidate not in tenant_candidates:
            tenant_candidates.append(candidate)

    endpoints: list[str] = []
    for tenant in tenant_candidates:
        endpoint = f"{parsed.scheme}://{host}/wday/cxs/{tenant}/{site}/jobs"
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = " | ".join(str(item) for item in value)
    text = str(value)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _slug_to_company(slug: str | None) -> str | None:
    cleaned = _clean_text(slug)
    if not cleaned:
        return None
    return cleaned.replace("-", " ").replace("_", " ").strip().title()


def _derive_company(source_config: dict[str, Any]) -> str | None:
    explicit = _clean_text(source_config.get("company"))
    if explicit and explicit.lower() not in _GENERIC_COMPANY_VALUES:
        return explicit

    job_board_url = _clean_text(source_config.get("job_board_url"))
    if job_board_url:
        parsed = urlparse(job_board_url)
        parts = [part for part in parsed.path.split("/") if part]
        for candidate in reversed(parts):
            lowered = candidate.lower()
            if lowered in {"en-us", "en-gb", "candidateexperience", "sites"}:
                continue
            if lowered in _GENERIC_COMPANY_VALUES:
                continue
            if lowered.startswith("cx_"):
                continue
            derived = _slug_to_company(candidate)
            if derived:
                return derived
        host_root = parsed.netloc.split(".")[0].lower()
        if host_root and host_root not in _GENERIC_COMPANY_VALUES:
            return _slug_to_company(host_root)

    endpoint_url = _clean_text(source_config.get("endpoint_url"))
    if endpoint_url:
        parsed = urlparse(endpoint_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 4:
            return _slug_to_company(parts[3]) or _slug_to_company(parts[2])
        host_root = parsed.netloc.split(".")[0].lower()
        if host_root and host_root not in _GENERIC_COMPANY_VALUES:
            return _slug_to_company(host_root)

    return explicit


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
    external_path = job.get("externalPath")
    if isinstance(external_path, str) and external_path.startswith("/") and base_url:
        return f"{base_url}{external_path}"

    req_id = _coalesce(job.get("reqId"), job.get("jobReqId"), job.get("id"))
    if base_url and isinstance(req_id, str):
        return f"{base_url}/job/{req_id}"
    return None


def _extract_discovered_url(job: dict[str, Any], source_config: dict[str, Any]) -> str | None:
    return _extract_apply_url(job, source_config)


def _extract_external_job_id(job: dict[str, Any]) -> str | None:
    candidate = _coalesce(
        job.get("reqId"),
        job.get("jobReqId"),
        job.get("id"),
        job.get("externalPath"),
        job.get("title"),
    )
    if isinstance(candidate, list):
        return _clean_text(candidate)
    return candidate


def _job_to_record(job: dict[str, Any], source_config: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean_text(_coalesce(job.get("title"), job.get("jobTitle"), job.get("name")))
    location = _extract_location(job)
    posted_at = _extract_posted_at(job)
    summary = _extract_summary(job)
    discovered_url = _extract_discovered_url(job, source_config)
    apply_url = _extract_apply_url(job, source_config)
    company_name = _derive_company(source_config)

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
            "company": company_name or "",
            "platform": "Workday",
            "board_url": _clean_text(source_config.get("job_board_url")) or "",
            "endpoint_url": _clean_text(source_config.get("endpoint_url")) or "",
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

    async def discover_from_board_url(
        self,
        job_board_url: str,
        limit: int = 20,
        since: datetime | None = None,
    ) -> tuple[str, DiscoveryPage]:
        candidates = derive_workday_candidate_endpoints(job_board_url)
        if not candidates:
            raise ValueError("Could not derive Workday endpoint candidates from job board URL")

        last_error: Exception | None = None
        for endpoint_url in candidates:
            try:
                page = await self.discover(
                    source_config={
                        "endpoint_url": endpoint_url,
                        "job_board_url": job_board_url,
                        "limit": limit,
                    },
                    since=since,
                )
                page.diagnostics.metadata["job_board_url"] = job_board_url
                page.diagnostics.metadata["resolved_endpoint_url"] = endpoint_url
                page.diagnostics.metadata["candidate_count"] = len(candidates)
                return endpoint_url, page
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Workday endpoint candidates succeeded")
