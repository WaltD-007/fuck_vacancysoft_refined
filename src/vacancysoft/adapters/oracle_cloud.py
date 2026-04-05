from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    SourceAdapter,
)
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats"]
PAGE_TIMEOUT_MS = 60_000


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_requisition(req: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _clean(req.get("Title") or req.get("title") or req.get("RequisitionTitle"))
    if not title:
        return None
    location = req.get("PrimaryLocation") or req.get("primaryLocation") or req.get("WorkLocation") or req.get("LocationCity")
    if isinstance(location, dict):
        location = location.get("descriptor")
    location = _clean(location)
    req_id = _clean(req.get("Id") or req.get("RequisitionNumber") or req.get("id"))
    board_url = str(board.get("url") or "").rstrip("/")
    job_url = f"{board_url}/job/{req_id}" if req_id else board_url
    posted = _clean(req.get("PostedDate") or req.get("postedDate"))
    company_name = lookup_company("oracle", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, job_url, posted]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=req_id or job_url,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted,
        summary_raw=None,
        discovered_url=job_url,
        apply_url=job_url,
        listing_payload=req,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.87,
        provenance={
            "adapter": "oracle",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "Oracle Cloud",
            "board_url": str(board.get("url") or ""),
        },
    )


def _extract_records_from_xhr(captured: list[dict[str, Any]], board: dict[str, Any]) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    for data in captured:
        items = data.get("items") or data.get("requisitionList") or data.get("RequisitionList") or data.get("value") or []
        for item in items:
            if isinstance(item, dict):
                record = _parse_requisition(item, board)
                if record:
                    records.append(record)
    return records


class OracleCloudAdapter(SourceAdapter):
    adapter_name = "oracle"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=False,
        supports_html=False,
        supports_browser=True,
        supports_site_rescue=False,
    )

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("OracleCloudAdapter requires job_board_url")
        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        board = {"url": board_url, "company": source_config.get("company")}
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "search_terms": search_terms})
        if cursor is not None:
            diagnostics.warnings.append("OracleCloudAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("OracleCloudAdapter does not enforce incremental sync at source. since was ignored.")
        started = time.perf_counter()
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    for term in search_terms:
                        captured: list[dict[str, Any]] = []
                        async def on_response(response: Any) -> None:
                            url = response.url
                            if response.status != 200:
                                return
                            if not any(key in url for key in ("recruitingCEJobRequisitions", "searchJobRequisitions", "recruitingJobRequisitions")):
                                return
                            try:
                                data = await response.json()
                                if isinstance(data, dict):
                                    captured.append(data)
                            except Exception:
                                diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1
                        page.on("response", on_response)
                        search_url = f"{board_url}?keyword={term}&sortBy=POSTING_DATES_DESC"
                        try:
                            await page.goto(search_url, wait_until="networkidle", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                        except Exception:
                            await page.goto(search_url, wait_until="domcontentloaded", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                            await page.wait_for_timeout(int(source_config.get("search_settle_ms", 5000)))
                        parsed = _extract_records_from_xhr(captured, board)
                        for record in parsed:
                            url = record.discovered_url or record.external_job_id
                            if not url or url in seen_urls:
                                continue
                            seen_urls.add(url)
                            all_records.append(record)
                        page.remove_listener("response", on_response)
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Oracle Cloud page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Oracle Cloud browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
