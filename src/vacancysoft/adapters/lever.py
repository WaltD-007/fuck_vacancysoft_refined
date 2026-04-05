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
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

API_BASE = "https://api.lever.co/v0/postings"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_posting(posting: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    categories = posting.get("categories") or {}
    location = None
    if isinstance(categories, dict):
        location = _clean(categories.get("location"))
        if not location:
            all_locations = categories.get("allLocations") or []
            if isinstance(all_locations, list) and all_locations:
                location = _clean(all_locations[0])
    if not location:
        location = _clean(posting.get("workplaceType"))
    contract_type = _clean((categories.get("commitment") if isinstance(categories, dict) else None))
    company_name = lookup_company("lever", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
    discovered_url = _clean(posting.get("hostedUrl")) or board["url"]
    completeness_fields = [_clean(posting.get("text")), location, discovered_url]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=_clean(posting.get("id")) or discovered_url,
        title_raw=_clean(posting.get("text")),
        location_raw=location,
        posted_at_raw=None,
        summary_raw=contract_type,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=posting,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "lever",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Lever",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
            "contract_type": contract_type,
        },
    )


class LeverAdapter(SourceAdapter):
    adapter_name = "lever"
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

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None) -> DiscoveryPage:
        slug = str(source_config.get("slug") or "").strip()
        if not slug:
            raise ValueError("Lever source_config requires slug")
        board = {
            "slug": slug,
            "company": source_config.get("company"),
            "url": str(source_config.get("job_board_url") or f"https://jobs.lever.co/{slug}").strip(),
        }
        diagnostics = AdapterDiagnostics(metadata={"slug": slug, "url": f"{API_BASE}/{slug}"})
        if cursor is not None:
            diagnostics.warnings.append("LeverAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("LeverAdapter does not enforce incremental sync at source. since was ignored.")

        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            response = await client.get(f"{API_BASE}/{slug}", params={"mode": "json"})
            response.raise_for_status()
            data = response.json()

        diagnostics.counters["status_code"] = int(response.status_code)
        jobs = [posting for posting in data if isinstance(posting, dict)] if isinstance(data, list) else []
        diagnostics.counters["jobs_seen"] = len(jobs)
        return DiscoveryPage(jobs=[_parse_posting(posting, board) for posting in jobs], next_cursor=None, diagnostics=diagnostics)
