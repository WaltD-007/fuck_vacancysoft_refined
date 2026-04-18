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
    PageCallback,
    SourceAdapter,
)
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def derive_workday_candidate_endpoints(job_board_url: str) -> list[str]:
    parsed = urlparse(job_board_url)
    host = parsed.netloc
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return []
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
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", str(value))).strip()
    return text or None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.I)


def _location_from_path(path: str | None) -> str | None:
    """Extract primary location from Workday externalPath URL slug.
    e.g. '/job/Hong-Kong/Analyst_R260177' → 'Hong Kong'
    """
    if not path:
        return None
    # Path format: /job/{Location-Slug}/{Title-Slug}_{ReqId}
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "job":
        slug = parts[1]
        # Convert slug to readable: "Hong-Kong" → "Hong Kong", "Chennai-Tamil-Nadu-India" → "Chennai Tamil Nadu India"
        return slug.replace("-", " ")
    return None


def _extract_location(job: dict[str, Any]) -> str | None:
    locations = job.get("locationsText")
    # If "N Locations", try to extract primary from the URL path instead
    if isinstance(locations, str) and _MULTI_LOC_RE.match(locations.strip()):
        from_path = _location_from_path(job.get("externalPath"))
        if from_path:
            return from_path
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
    return _clean_text(_coalesce(job.get("bulletFields"), job.get("jobDescription"), job.get("description"), job.get("shortDescription")))


def _extract_posted_at(job: dict[str, Any]) -> str | None:
    value = _coalesce(job.get("postedOn"), job.get("publicationDate"), job.get("postedDate"))
    return str(value) if value is not None else None


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


def _extract_external_job_id(job: dict[str, Any]) -> str | None:
    return _clean_text(_coalesce(job.get("reqId"), job.get("jobReqId"), job.get("id"), job.get("externalPath"), job.get("title")))


def _job_to_record(job: dict[str, Any], source_config: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean_text(_coalesce(job.get("title"), job.get("jobTitle"), job.get("name")))
    location = _extract_location(job)
    posted_at = _extract_posted_at(job)
    summary = _extract_summary(job)
    apply_url = _extract_apply_url(job, source_config)
    company_name = lookup_company("workday", board_url=source_config.get("job_board_url"), explicit_company=source_config.get("company"))
    completeness_fields = [title, location, posted_at, apply_url]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=_extract_external_job_id(job),
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=apply_url,
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
    capabilities = AdapterCapabilities(supports_discovery=True, supports_detail_fetch=False, supports_healthcheck=False, supports_pagination=True, supports_incremental_sync=False, supports_api=True, supports_html=False, supports_browser=False, supports_site_rescue=False)

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        endpoint_url = str(source_config.get("endpoint_url") or "").strip()
        if not endpoint_url:
            raise ValueError("Workday source_config requires endpoint_url")
        limit = int(source_config.get("limit", 20))
        max_pages = int(source_config.get("max_pages", 250))
        offset = int(cursor or 0)

        diagnostics = AdapterDiagnostics(metadata={"endpoint_url": endpoint_url, "offset": offset, "limit": limit})
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        page_num = 0
        server_total: int | None = None

        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 30))) as client:
            while page_num < max_pages:
                payload: dict[str, Any] = {"limit": limit, "offset": offset, "searchText": source_config.get("search_text", "")}
                if since is not None:
                    payload["postedAfter"] = since.date().isoformat()

                response = await client.post(endpoint_url, json=payload)
                response.raise_for_status()
                response_json = response.json()

                # Use server-reported total to know when to stop
                if server_total is None and "total" in response_json:
                    server_total = int(response_json["total"])
                    diagnostics.metadata["server_total"] = server_total

                jobs = _extract_jobs(response_json)
                diagnostics.counters["status_code"] = int(response.status_code)
                page_num += 1

                if not jobs:
                    break

                # Dedup: stop if API is wrapping around
                new_records: list[DiscoveredJobRecord] = []
                for job in jobs:
                    rec = _job_to_record(job, source_config)
                    url_key = rec.discovered_url or rec.external_job_id
                    if url_key and url_key in seen_urls:
                        continue
                    if url_key:
                        seen_urls.add(url_key)
                    new_records.append(rec)

                if not new_records:
                    # Entire page was duplicates — API is wrapping around
                    break

                all_records.extend(new_records)

                if on_page_scraped and new_records:
                    try:
                        on_page_scraped(page_num, new_records, all_records)
                    except Exception:
                        pass

                # Stop if this page returned fewer than limit (no more results)
                if len(jobs) < limit:
                    break

                # Stop if we've fetched everything the server says exists
                if server_total is not None and offset + limit >= server_total:
                    break

                offset += limit

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["pages_fetched"] = page_num
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)

    async def discover_from_board_url(self, job_board_url: str, limit: int = 20, since: datetime | None = None) -> tuple[str, DiscoveryPage]:
        candidates = derive_workday_candidate_endpoints(job_board_url)
        if not candidates:
            raise ValueError("Could not derive Workday endpoint candidates from job board URL")
        last_error: Exception | None = None
        company_name = lookup_company("workday", board_url=job_board_url)
        for endpoint_url in candidates:
            try:
                page = await self.discover(source_config={"endpoint_url": endpoint_url, "job_board_url": job_board_url, "company": company_name, "limit": limit}, since=since)
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
