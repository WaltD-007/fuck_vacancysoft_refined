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
    PageCallback,
    SourceAdapter,
)
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

# BambooHR embeds job data as JSON inside its careers page.
# Public endpoint: https://{subdomain}.bamboohr.com/careers/list
CAREERS_LIST = "https://{subdomain}.bamboohr.com/careers/list"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_location(job: dict[str, Any]) -> str | None:
    city = _clean(job.get("city"))
    state = _clean(job.get("state"))
    country = _clean(job.get("country"))
    location = _clean(job.get("location", {}).get("city") if isinstance(job.get("location"), dict) else job.get("location"))
    if city or state or country:
        parts = [p for p in [city, state, country] if p]
        return ", ".join(parts)
    return location


def _parse_job(job: dict[str, Any], subdomain: str, company_name: str) -> DiscoveredJobRecord:
    title = _clean(job.get("jobOpeningName"))
    location = _build_location(job)
    job_id = _clean(job.get("id"))
    department = _clean(job.get("departmentLabel"))
    employment_type = _clean(job.get("employmentStatusLabel"))

    discovered_url = f"https://{subdomain}.bamboohr.com/careers/{job_id}" if job_id else None

    summary_parts = [p for p in [department, employment_type] if p]
    summary_raw = " | ".join(summary_parts) if summary_parts else None

    completeness_fields = [title, location, discovered_url, department]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=str(job_id) if job_id else discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=_clean(job.get("datePosted") or job.get("createdDate")),
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.90,
        provenance={
            "adapter": "bamboohr",
            "method": ExtractionMethod.API.value,
            "company": company_name,
            "platform": "BambooHR",
            "board_url": f"https://{subdomain}.bamboohr.com/careers",
            "subdomain": subdomain,
            "department": department,
        },
    )


class BambooHRAdapter(SourceAdapter):
    adapter_name = "bamboohr"
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
        board_url = str(source_config.get("job_board_url") or "").strip()
        subdomain = str(source_config.get("slug") or "").strip()

        # Derive subdomain from board URL if not provided
        if not subdomain and board_url:
            # e.g. https://acolin.bamboohr.com/careers -> acolin
            import re
            m = re.search(r"https?://([^.]+)\.bamboohr\.com", board_url)
            if m:
                subdomain = m.group(1)

        if not subdomain:
            raise ValueError("BambooHR source_config requires slug or a bamboohr.com job_board_url")

        company_name = lookup_company(
            "bamboohr",
            board_url=board_url or f"https://{subdomain}.bamboohr.com/careers",
            slug=subdomain,
            explicit_company=source_config.get("company"),
        )

        api_url = CAREERS_LIST.format(subdomain=subdomain)
        timeout_seconds = float(source_config.get("timeout_seconds", 20))
        diagnostics = AdapterDiagnostics(metadata={"subdomain": subdomain, "api_url": api_url})

        if cursor is not None:
            diagnostics.warnings.append("BambooHRAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("BambooHRAdapter does not support incremental sync. since was ignored.")

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(api_url)
            response.raise_for_status()
            data = response.json()

        diagnostics.counters["status_code"] = int(response.status_code)

        # BambooHR list endpoint returns {"result": [...]} or a bare list
        jobs_list: list[dict[str, Any]] = []
        if isinstance(data, list):
            jobs_list = data
        elif isinstance(data, dict):
            jobs_list = data.get("result") or data.get("jobs") or data.get("data") or []

        records = [_parse_job(job, subdomain, company_name) for job in jobs_list if isinstance(job, dict)]
        diagnostics.counters["jobs_seen"] = len(records)

        if on_page_scraped and records:
            try:
                on_page_scraped(1, records, records)
            except Exception:
                pass

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
