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
    PageCallback,
    SourceAdapter,
)
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

API_BASE = "https://api.lever.co/v0/postings"

# Lever board URLs follow the pattern https://jobs.lever.co/<slug>[/...].
# When a source's config_blob has `job_board_url` but no `slug` (happens
# when the source was registered via the generic URL-detection flow rather
# than an explicit Lever integration), we can derive the slug from the URL
# rather than hard-failing. This regex extracts group 1 as the slug.
_LEVER_SLUG_RE = re.compile(r"^https?://jobs\.lever\.co/([^/?#]+)", re.IGNORECASE)


def _derive_slug_from_url(url: str | None) -> str | None:
    """Extract the Lever board slug from a jobs.lever.co URL.

    Returns None if the URL is empty, not a Lever URL, or malformed.
    Used as a fallback when `source_config["slug"]` is missing.
    """
    if not url:
        return None
    m = _LEVER_SLUG_RE.match(url.strip())
    if not m:
        return None
    return m.group(1).strip() or None


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
    # workplaceType ("remote", "hybrid", "on-site") is NOT a location — keep it separate
    # from location_raw so the normaliser doesn't try to geocode "Remote" as a city.
    workplace_type = _clean(posting.get("workplaceType"))
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
            "workplace_type": workplace_type,
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
        complete_coverage_per_run=True,
    )

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        # Prefer explicit `slug`; fall back to extracting it from
        # `job_board_url` when the URL is a Lever one. This covers sources
        # that were registered via the generic URL-detection flow (which
        # only populated `job_board_url`) — previously these hard-failed
        # with ValueError at discover-time, e.g. Octopus Energy, Simply
        # Business, Titan on 2026-04-20.
        slug = str(source_config.get("slug") or "").strip()
        if not slug:
            slug = _derive_slug_from_url(source_config.get("job_board_url")) or ""
        if not slug:
            url_hint = source_config.get("job_board_url") or "(none)"
            raise ValueError(
                f"Lever source_config requires slug — none provided and "
                f"job_board_url ({url_hint}) is not a jobs.lever.co URL"
            )
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

        # Timeout bumped from 20 → 60 s on 2026-04-20. Lever returns all
        # postings in one uncompressed JSON response — 200+ posting boards
        # (Contentsquare/Hotjar: 188, Farfetch) occasionally needed more
        # than 20 s. 60 s keeps bounded but handles the large boards.
        # Per-source override via `source_config["timeout_seconds"]` still works.
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 60))) as client:
            response = await client.get(f"{API_BASE}/{slug}", params={"mode": "json"})
            response.raise_for_status()
            data = response.json()

        diagnostics.counters["status_code"] = int(response.status_code)
        jobs = [posting for posting in data if isinstance(posting, dict)] if isinstance(data, list) else []
        diagnostics.counters["jobs_seen"] = len(jobs)
        return DiscoveryPage(jobs=[_parse_posting(posting, board) for posting in jobs], next_cursor=None, diagnostics=diagnostics)
