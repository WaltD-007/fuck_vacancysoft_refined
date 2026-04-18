from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

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


PAGE_TIMEOUT_MS = 45_000
SEARCH_PATH = "/jobs"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _make_record(job: dict[str, Any], board: dict[str, Any], source: str) -> DiscoveredJobRecord | None:
    title = _clean(job.get("title") or job.get("name") or job.get("jobTitle"))
    discovered_url = _clean(job.get("url") or job.get("absolute_url") or job.get("applyUrl"))
    if discovered_url and not discovered_url.startswith("http"):
        discovered_url = urljoin(str(board.get("url") or ""), discovered_url)
    if not title and not discovered_url:
        return None
    location = _clean(job.get("location") or job.get("city") or job.get("office"))
    posted_at = _clean(job.get("postedAt") or job.get("createdAt") or job.get("updatedAt"))
    company_name = lookup_company("beamery", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=_clean(job.get("id")) or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=None,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.76,
        provenance={
            "adapter": "beamery",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "Beamery",
            "board_url": str(board.get("url") or ""),
            "source": source,
        },
    )


def _walk_json(node: Any, board: dict[str, Any], source: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    if isinstance(node, dict):
        if any(key in node for key in ("title", "jobTitle", "applyUrl", "absolute_url")):
            record = _make_record(node, board, source)
            if record:
                records.append(record)
        for value in node.values():
            records.extend(_walk_json(value, board, source))
    elif isinstance(node, list):
        for item in node:
            records.extend(_walk_json(item, board, source))
    return records


class BeameryAdapter(SourceAdapter):
    adapter_name = "beamery"
    capabilities = AdapterCapabilities(supports_discovery=True, supports_detail_fetch=False, supports_healthcheck=False, supports_pagination=False, supports_incremental_sync=False, supports_api=False, supports_html=False, supports_browser=True, supports_site_rescue=False)

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").rstrip("/")
        if not board_url:
            raise ValueError("BeameryAdapter requires job_board_url")
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        if cursor is not None:
            diagnostics.warnings.append("BeameryAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("BeameryAdapter does not enforce incremental sync at source. since was ignored.")
        board = {"url": board_url, "company": source_config.get("company")}
        records: list[DiscoveredJobRecord] = []
        seen: set[str] = set()
        started = time.perf_counter()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(f"{board_url}{SEARCH_PATH}", wait_until="domcontentloaded", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                    scripts = await page.query_selector_all("script[type='application/ld+json'], script#__NEXT_DATA__")
                    for idx, script in enumerate(scripts):
                        try:
                            payload = json.loads(await script.inner_text())
                        except Exception:
                            continue
                        for record in _walk_json(payload, board, f"script_{idx}"):
                            key = record.discovered_url or record.external_job_id or ""
                            if key and key not in seen:
                                seen.add(key)
                                records.append(record)
                finally:
                    await page.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Beamery page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Beamery browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(records)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
