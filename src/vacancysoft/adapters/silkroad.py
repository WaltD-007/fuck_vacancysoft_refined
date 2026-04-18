from __future__ import annotations

import time
import xml.etree.ElementTree as ET
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


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_company_prefix(url: str) -> str:
    """Extract company prefix from SilkRoad URL.

    e.g. https://opco-openhire.silkroad.com/ -> opco
    """
    host = urlparse(url).netloc.lower()
    # Pattern: {company}-openhire.silkroad.com
    parts = host.split("-openhire")
    if len(parts) >= 2:
        return parts[0]
    # Fallback: first subdomain part
    return host.split(".")[0]


def _build_api_url(board_url: str) -> str:
    """Build the RSS API URL from a SilkRoad board URL."""
    parsed = urlparse(board_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/api/index.cfm"


def _parse_rss_item(
    item: ET.Element,
    company_name: str,
    board_url: str,
    ns: dict[str, str],
) -> DiscoveredJobRecord | None:
    title = _clean(item.findtext("title"))
    if not title or len(title) < 4:
        return None

    link = _clean(item.findtext("link"))
    description = _clean(item.findtext("description"))
    pub_date = _clean(item.findtext("pubDate"))
    location = _clean(item.findtext("location"))

    # Some SilkRoad feeds put location in a custom namespace or in the description
    if not location and description:
        # Try to extract location from description if it contains "Location:" prefix
        for line in description.split("\n"):
            stripped = line.strip()
            if stripped.lower().startswith("location:"):
                location = stripped[len("location:"):].strip()
                break

    completeness_fields = [title, location, link, pub_date]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=link or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=pub_date,
        summary_raw=description[:500] if description else None,
        discovered_url=link,
        apply_url=link,
        listing_payload={
            "title": title,
            "link": link,
            "description": description,
            "pubDate": pub_date,
            "location": location,
        },
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.88,
        provenance={
            "adapter": "silkroad",
            "method": ExtractionMethod.API.value,
            "company": company_name,
            "platform": "SilkRoad OpenHire",
            "board_url": board_url,
        },
    )


class SilkRoadAdapter(SourceAdapter):
    adapter_name = "silkroad"
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
        if not board_url:
            raise ValueError("SilkRoad source_config requires job_board_url")

        company_name = lookup_company(
            "silkroad",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )
        api_url = _build_api_url(board_url)
        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "api_url": api_url})
        t0 = time.monotonic()

        if cursor is not None:
            diagnostics.warnings.append("SilkRoadAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("SilkRoadAdapter does not enforce incremental sync at source. since was ignored.")

        params = {
            "fuseaction": "app.getJobListings",
            "FORMAT": "rss",
            "JOBPLACEMENT": "external",
            "KEYWORD": "",
            "LANGUAGE": "en_US",
            "VERSION": "1",
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()

        diagnostics.counters["status_code"] = int(response.status_code)

        # Parse RSS/XML response
        records: list[DiscoveredJobRecord] = []
        try:
            root = ET.fromstring(response.text)
            ns = {}  # No namespace expected for standard RSS

            # RSS items are at channel/item
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else root.findall(".//item")

            diagnostics.counters["items_in_feed"] = len(items)

            for item in items:
                rec = _parse_rss_item(item, company_name, board_url, ns)
                if rec:
                    records.append(rec)
        except ET.ParseError as exc:
            diagnostics.errors.append(f"XML parse error: {exc}")

        diagnostics.counters["jobs_parsed"] = len(records)
        diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)

        if on_page_scraped:
            await on_page_scraped(1, records, records)

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
