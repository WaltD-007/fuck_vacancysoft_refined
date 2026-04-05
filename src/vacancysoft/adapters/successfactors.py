from __future__ import annotations

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
from vacancysoft.browser import browser_session
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance"]
PAGE_TIMEOUT_MS = 60_000


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _make_record(title: str, href: str, board: dict[str, Any]) -> DiscoveredJobRecord:
    company_name = lookup_company("successfactors", board_url=board.get("url"), explicit_company=board.get("company"))
    return DiscoveredJobRecord(
        external_job_id=href or title,
        title_raw=_clean(title),
        location_raw=None,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload=None,
        completeness_score=0.5 if href else 0.25,
        extraction_confidence=0.72,
        provenance={
            "adapter": "successfactors",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "SuccessFactors",
            "board_url": str(board.get("url") or ""),
        },
    )


class SuccessFactorsAdapter(SourceAdapter):
    adapter_name = "successfactors"
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
            raise ValueError("SuccessFactorsAdapter requires job_board_url")
        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "search_terms": search_terms})
        if cursor is not None:
            diagnostics.warnings.append("SuccessFactorsAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("SuccessFactorsAdapter does not enforce incremental sync at source. since was ignored.")
        started = time.perf_counter()
        board = {"url": board_url, "company": source_config.get("company")}
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        try:
            async with async_playwright() as playwright:
                async with browser_session(playwright) as (_browser, context):
                    page = await context.new_page()
                    try:
                        for term in search_terms:
                            search_url = f"{board_url}&navBarLevel=JOB_SEARCH" if "?" in board_url else f"{board_url}?navBarLevel=JOB_SEARCH"
                            try:
                                await page.goto(search_url, wait_until="networkidle", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                                await page.wait_for_timeout(2000)
                            except Exception:
                                await page.goto(search_url, wait_until="domcontentloaded", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                                await page.wait_for_timeout(4000)
                            try:
                                search_input = await page.query_selector("input#keywordInput, input[name*='keyword' i], input[id*='keyword' i], input[placeholder*='search' i], input[placeholder*='title' i]")
                                if search_input:
                                    await search_input.click()
                                    await search_input.fill(term)
                                    await search_input.press("Enter")
                                    await page.wait_for_timeout(3000)
                            except Exception:
                                diagnostics.counters["search_box_misses"] = diagnostics.counters.get("search_box_misses", 0) + 1
                            job_elements = await page.query_selector_all(".jobResultItem, .JobResultItem, [class*='jobTitle'], a[href*='job_req_id'], .position-title")
                            for el in job_elements:
                                try:
                                    title_el = await el.query_selector("a, span, h3, h4")
                                    title = _clean(await (title_el or el).inner_text())
                                    link = await el.query_selector("a")
                                    href = await link.get_attribute("href") if link else await el.get_attribute("href")
                                    href = _clean(href)
                                    if href and not href.startswith("http"):
                                        base = "/".join(board_url.split("/")[:3])
                                        href = f"{base}{href}"
                                    if title and href and href not in seen_urls:
                                        seen_urls.add(href)
                                        all_records.append(_make_record(title, href, board))
                                except Exception:
                                    diagnostics.counters["element_parse_failures"] = diagnostics.counters.get("element_parse_failures", 0) + 1
                    finally:
                        await page.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"SuccessFactors page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"SuccessFactors browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
