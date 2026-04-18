from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

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


FEED_PATHS = ("/xml", "/xml?language=en", "/xml?language=en&displayMode=full")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(parent: ET.Element, *names: str) -> str | None:
    for name in names:
        node = parent.find(name)
        if node is not None and node.text:
            return _clean(node.text)
    return None


def _parse_job(job: ET.Element, board: dict[str, Any]) -> DiscoveredJobRecord:
    title = _first_text(job, "name", "title", "position")
    location = _first_text(job, "office", "location", "city")
    posted_at = _first_text(job, "createdAt", "created_at", "date")
    job_id = _first_text(job, "id", "jobId", "job_id")
    link = _first_text(job, "url", "link", "applyUrl", "apply_url")
    discovered_url = link if link and link.startswith("http") else urljoin(str(board.get("url") or ""), link or "")
    department = _first_text(job, "department", "team")
    employment_type = _first_text(job, "employmentType", "employment_type")
    summary = " | ".join(part for part in [department, employment_type] if part) or None
    company_name = lookup_company("personio", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=job_id or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload={child.tag: (child.text or "") for child in job},
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "personio",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Personio",
            "board_url": str(board.get("url") or ""),
        },
    )


class PersonioAdapter(SourceAdapter):
    adapter_name = "personio"
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
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").rstrip("/")
        if not board_url:
            raise ValueError("PersonioAdapter requires job_board_url")
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        if cursor is not None:
            diagnostics.warnings.append("PersonioAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("PersonioAdapter does not enforce incremental sync at source. since was ignored.")
        xml_text = None
        tried: list[str] = []
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            for suffix in FEED_PATHS:
                candidate = f"{board_url}{suffix}"
                tried.append(candidate)
                response = await client.get(candidate)
                if response.status_code == 200 and "<" in response.text:
                    xml_text = response.text
                    diagnostics.metadata["feed_url"] = candidate
                    diagnostics.counters["status_code"] = int(response.status_code)
                    break
        if not xml_text:
            diagnostics.errors.append("No Personio XML feed responded successfully.")
            diagnostics.metadata["tried_feed_urls"] = tried
            return DiscoveryPage(jobs=[], next_cursor=None, diagnostics=diagnostics)
        root = ET.fromstring(xml_text)
        jobs = root.findall(".//position") or root.findall(".//job") or root.findall(".//item")
        board = {"url": board_url, "company": source_config.get("company")}
        records = [_parse_job(job, board) for job in jobs]
        diagnostics.counters["jobs_seen"] = len(records)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
