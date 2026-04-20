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

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_salary(compensation: dict[str, Any] | None) -> str | None:
    if not compensation:
        return None
    tiers = compensation.get("compensationTiers") or []
    if not tiers or not isinstance(tiers[0], dict):
        return None
    tier = tiers[0]
    low = tier.get("minValue")
    high = tier.get("maxValue")
    currency = _clean(tier.get("currency")) or ""
    interval = _clean(tier.get("interval")) or ""
    if low and high:
        return f"{currency}{low:,} - {currency}{high:,} {interval}".strip()
    return None


def _extract_location(job: dict[str, Any]) -> str | None:
    location = _clean(job.get("location"))
    if location:
        return location
    address = job.get("address")
    if isinstance(address, dict):
        postal = address.get("postalAddress", address)
        if isinstance(postal, dict):
            parts = [
                _clean(postal.get("addressLocality")),
                _clean(postal.get("addressRegion")),
                _clean(postal.get("addressCountry")),
            ]
            filtered = [part for part in parts if part]
            return ", ".join(filtered) if filtered else None
    if isinstance(address, str):
        return _clean(address)
    return None


def _parse_job(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean(job.get("title"))
    location = _extract_location(job)
    discovered_url = _clean(job.get("jobUrl"))
    posted_at = _clean(job.get("publishedAt"))
    contract_type = _clean(job.get("employmentType"))
    salary = _format_salary(job.get("compensation"))
    company_name = lookup_company("ashby", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))

    summary_parts = [part for part in [salary, contract_type] if part]
    summary_raw = " | ".join(summary_parts) if summary_parts else None
    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=_clean(job.get("id")) or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "ashby",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Ashby",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
            "salary": salary,
            "contract_type": contract_type,
        },
    )


class AshbyAdapter(SourceAdapter):
    adapter_name = "ashby"
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
        slug = str(source_config.get("slug") or "").strip()
        if not slug:
            raise ValueError("Ashby source_config requires slug")

        board = {
            "slug": slug,
            "company": source_config.get("company"),
            "url": str(source_config.get("job_board_url") or f"https://jobs.ashbyhq.com/{slug}").strip(),
        }
        diagnostics = AdapterDiagnostics(metadata={"slug": slug, "url": f"{API_BASE}/{slug}"})
        if cursor is not None:
            diagnostics.warnings.append("AshbyAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("AshbyAdapter does not enforce incremental sync at source. since was ignored.")

        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            response = await client.get(f"{API_BASE}/{slug}", params={"includeCompensation": "true"})
            response.raise_for_status()
            data = response.json()

        jobs = [job for job in (data.get("jobs") or []) if isinstance(job, dict) and job.get("isListed", True)]
        diagnostics.counters["status_code"] = int(response.status_code)
        diagnostics.counters["jobs_seen"] = len(jobs)
        return DiscoveryPage(jobs=[_parse_job(job, board) for job in jobs], next_cursor=None, diagnostics=diagnostics)
