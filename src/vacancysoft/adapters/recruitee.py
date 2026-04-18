from __future__ import annotations

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


API_SUFFIX = "/api/offers"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_base_url(source_config: dict[str, Any]) -> str:
    base_url = _clean(source_config.get("job_board_url") or source_config.get("url"))
    if not base_url:
        raise ValueError("RecruiteeAdapter requires job_board_url")
    return base_url.rstrip("/")


def _api_url(base_url: str) -> str:
    return f"{base_url}{API_SUFFIX}"


def _job_url(base_url: str, offer: dict[str, Any]) -> str | None:
    careers_url = _clean(offer.get("careers_url"))
    if careers_url:
        if careers_url.startswith("http"):
            return careers_url
        return f"{base_url}/{careers_url.lstrip('/')}"
    slug = _clean(offer.get("slug"))
    if slug:
        return f"{base_url}/o/{slug}"
    return None


def _location_raw(offer: dict[str, Any]) -> str | None:
    options = offer.get("location") or offer.get("locations") or []
    if isinstance(options, dict):
        parts = [_clean(options.get("city")), _clean(options.get("country")), _clean(options.get("name"))]
        return ", ".join(part for part in parts if part) or None
    if isinstance(options, list):
        for item in options:
            if isinstance(item, dict):
                parts = [_clean(item.get("city")), _clean(item.get("country")), _clean(item.get("name"))]
                text = ", ".join(part for part in parts if part)
                if text:
                    return text
            else:
                text = _clean(item)
                if text:
                    return text
    return _clean(offer.get("location_name"))


def _summary_raw(offer: dict[str, Any]) -> str | None:
    parts: list[str] = []
    category = offer.get("category") or {}
    if isinstance(category, dict):
        name = _clean(category.get("name"))
        if name:
            parts.append(name)
    department = offer.get("department") or {}
    if isinstance(department, dict):
        name = _clean(department.get("name"))
        if name:
            parts.append(name)
    employment_type = _clean(offer.get("employment_type"))
    if employment_type:
        parts.append(employment_type)
    return " | ".join(parts) if parts else None


def _parse_offer(offer: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    title = _clean(offer.get("title"))
    discovered_url = _job_url(str(board.get("url") or ""), offer)
    location = _location_raw(offer)
    posted_at = _clean(offer.get("created_at") or offer.get("published_at") or offer.get("updated_at"))
    summary = _summary_raw(offer)
    company_name = lookup_company(
        "recruitee",
        board_url=board.get("url"),
        explicit_company=board.get("company"),
    )
    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=_clean(offer.get("id")) or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=offer,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.94,
        provenance={
            "adapter": "recruitee",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Recruitee",
            "board_url": str(board.get("url") or ""),
        },
    )


class RecruiteeAdapter(SourceAdapter):
    adapter_name = "recruitee"
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
        base_url = _infer_base_url(source_config)
        board = {
            "url": base_url,
            "company": source_config.get("company"),
        }
        diagnostics = AdapterDiagnostics(metadata={"board_url": base_url, "api_url": _api_url(base_url)})
        if cursor is not None:
            diagnostics.warnings.append("RecruiteeAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("RecruiteeAdapter does not enforce incremental sync at source. since was ignored.")
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            response = await client.get(_api_url(base_url), params={"limit": 500})
            response.raise_for_status()
            data = response.json()
        offers = data.get("offers") or data.get("data") or data if isinstance(data, list) else []
        jobs = [offer for offer in offers if isinstance(offer, dict) and offer.get("published", True)]
        diagnostics.counters["status_code"] = int(response.status_code)
        diagnostics.counters["jobs_seen"] = len(jobs)
        return DiscoveryPage(jobs=[_parse_offer(offer, board) for offer in jobs], next_cursor=None, diagnostics=diagnostics)
