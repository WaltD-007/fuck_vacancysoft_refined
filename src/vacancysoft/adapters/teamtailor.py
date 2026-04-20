"""Teamtailor adapter — uses the public RSS feed at {board_url}/jobs.rss"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
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

_TT_NS = {"tt": "https://teamtailor.com/locations"}


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return (el.text or "").strip() or None


def _parse_location(item: ET.Element) -> str | None:
    """Extract city + country from tt:locations."""
    loc = item.find(".//tt:location", _TT_NS)
    if loc is None:
        return None
    city = _text(loc.find("tt:city", _TT_NS))
    country = _text(loc.find("tt:country", _TT_NS))
    if city and country:
        return f"{city}, {country}"
    return city or country


def _parse_item(item: ET.Element, board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _text(item.find("title"))
    link = _text(item.find("link"))
    if not title or not link:
        return None

    location = _parse_location(item)
    pub_date = _text(item.find("pubDate"))
    description = _text(item.find("description"))
    department = _text(item.find("tt:department", _TT_NS))
    role_level = _text(item.find("tt:role", _TT_NS))
    remote_status = _text(item.find("remoteStatus"))

    company_name = lookup_company(
        "teamtailor",
        board_url=board.get("url"),
        slug=board.get("slug"),
        explicit_company=board.get("company"),
    )

    # Build a clean summary from department + role level + remote status
    summary_parts = []
    if department:
        summary_parts.append(f"Department: {department}")
    if role_level:
        summary_parts.append(f"Level: {role_level}")
    if remote_status:
        summary_parts.append(f"Remote: {remote_status}")
    summary = " | ".join(summary_parts) if summary_parts else None

    return DiscoveredJobRecord(
        external_job_id=link,
        title_raw=title,
        location_raw=location,
        posted_at_raw=pub_date,
        summary_raw=summary,
        discovered_url=link,
        apply_url=link,
        listing_payload={"description_html": description} if description else None,
        completeness_score=0.8 if location else 0.6,
        extraction_confidence=0.85,
        provenance={
            "adapter": "teamtailor",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Teamtailor",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
        },
    )


class TeamtailorAdapter(SourceAdapter):
    adapter_name = "teamtailor"
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
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip().rstrip("/")
        if not board_url:
            raise ValueError("TeamtailorAdapter requires job_board_url")

        # Derive RSS URL — strip /jobs suffix if present, then add /jobs.rss
        base = board_url.rstrip("/")
        if base.endswith("/jobs"):
            base = base[:-5]
        rss_url = f"{base}/jobs.rss"

        slug = source_config.get("slug")
        company = source_config.get("company")
        board = {"url": board_url, "slug": slug, "company": company}

        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "rss_url": rss_url})
        started = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(rss_url)
                resp.raise_for_status()

            diagnostics.metadata["http_status"] = resp.status_code
            root = ET.fromstring(resp.text)
            items = root.findall(".//item")
            diagnostics.counters["rss_items"] = len(items)

            records: list[DiscoveredJobRecord] = []
            seen_urls: set[str] = set()

            for item in items:
                rec = _parse_item(item, board)
                if rec and rec.discovered_url not in seen_urls:
                    seen_urls.add(rec.discovered_url)
                    records.append(rec)

            if on_page_scraped and records:
                try:
                    on_page_scraped(1, records, records)
                except Exception:
                    pass

        except Exception as exc:
            diagnostics.errors.append(f"TeamtailorAdapter error: {exc}")
            raise

        diagnostics.counters["jobs_seen"] = len(records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
