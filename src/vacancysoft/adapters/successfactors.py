from __future__ import annotations

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
    SourceAdapter,
)
from vacancysoft.browser import browser_session
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance"]
PAGE_TIMEOUT_MS = 60_000
JOB_SELECTORS = [
    ".jobResultItem",
    ".JobResultItem",
    "[class*='jobTitle']",
    "a[href*='job_req_id']",
    ".position-title",
    "a[href*='career/job']",
    "a[href*='job']",
]
SEARCH_INPUT_SELECTOR = (
    "input#keywordInput, input[name*='keyword' i], input[id*='keyword' i], "
    "input[placeholder*='search' i], input[placeholder*='title' i]"
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _absolute_url(href: str | None, board_url: str) -> str | None:
    href = _clean(href)
    if not href:
        return None
    if href.startswith("http"):
        return href
    return urljoin(board_url, href)


def _make_record(title: str, href: str, board: dict[str, Any], *, source: str | None = None) -> DiscoveredJobRecord:
    company_name = lookup_company("successfactors", board_url=board.get("url"), explicit_company=board.get("company"))
    listing_payload = {"source": source} if source else None
    return DiscoveredJobRecord(
        external_job_id=href or title,
        title_raw=_clean(title),
        location_raw=None,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload=listing_payload,
        completeness_score=0.5 if href else 0.25,
        extraction_confidence=0.72,
        provenance={
            "adapter": "successfactors",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "SuccessFactors",
            "board_url": str(board.get("url") or ""),
            "source": source or "unknown",
        },
    )


async def _collect_anchor_samples(scope: Any, board_url: str, limit: int = 8) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    try:
        anchors = await scope.query_selector_all("a")
    except Exception:
        return samples
    for anchor in anchors[:limit]:
        try:
            text = _clean(await anchor.inner_text()) or ""
            href = _absolute_url(await anchor.get_attribute("href"), board_url) or ""
            if text or href:
                samples.append({"text": text[:120], "href": href})
        except Exception:
            continue
    return samples


async def _diagnose_page(page: Any, diagnostics: AdapterDiagnostics, board_url: str, label: str) -> None:
    try:
        diagnostics.metadata[f"{label}_page_url"] = page.url
    except Exception:
        pass
    try:
        diagnostics.metadata[f"{label}_page_title"] = await page.title()
    except Exception:
        pass
    try:
        frames = page.frames
        diagnostics.counters[f"{label}_frame_count"] = len(frames)
        diagnostics.metadata[f"{label}_frame_urls"] = [frame.url for frame in frames[:8]]
    except Exception:
        pass
    try:
        anchors = await page.query_selector_all("a")
        diagnostics.counters[f"{label}_anchor_count"] = len(anchors)
        diagnostics.metadata[f"{label}_anchor_samples"] = await _collect_anchor_samples(page, board_url)
    except Exception:
        pass


async def _extract_records_from_scope(
    scope: Any,
    board_url: str,
    board: dict[str, Any],
    diagnostics: AdapterDiagnostics,
    *,
    source: str,
) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    for selector in JOB_SELECTORS:
        try:
            elements = await scope.query_selector_all(selector)
        except Exception:
            continue
        if not elements:
            continue
        diagnostics.metadata[f"{source}_selector_used"] = selector
        diagnostics.counters[f"{source}_elements_seen"] = len(elements)
        for el in elements:
            try:
                title_el = await el.query_selector("a, span, h2, h3, h4, h5")
                title = _clean(await (title_el or el).inner_text())
                link = await el.query_selector("a")
                href = await link.get_attribute("href") if link else await el.get_attribute("href")
                href = _absolute_url(href, board_url)
                if title and href:
                    records.append(_make_record(title, href, board, source=source))
            except Exception:
                diagnostics.counters["element_parse_failures"] = diagnostics.counters.get("element_parse_failures", 0) + 1
        if records:
            return records
    return records


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

                            await _diagnose_page(page, diagnostics, board_url, f"term_{term}")

                            try:
                                search_input = await page.query_selector(SEARCH_INPUT_SELECTOR)
                                if search_input:
                                    await search_input.click()
                                    try:
                                        await search_input.press("Meta+A")
                                    except Exception:
                                        pass
                                    await search_input.fill(term)
                                    await search_input.press("Enter")
                                    await page.wait_for_timeout(3000)
                                    diagnostics.counters["search_terms_applied"] = diagnostics.counters.get("search_terms_applied", 0) + 1
                                else:
                                    diagnostics.counters["search_box_misses"] = diagnostics.counters.get("search_box_misses", 0) + 1
                            except Exception:
                                diagnostics.counters["search_box_misses"] = diagnostics.counters.get("search_box_misses", 0) + 1

                            parsed = await _extract_records_from_scope(page, board_url, board, diagnostics, source="page")
                            if not parsed:
                                for index, frame in enumerate(page.frames):
                                    if frame == page.main_frame:
                                        continue
                                    try:
                                        frame_records = await _extract_records_from_scope(
                                            frame,
                                            board_url,
                                            board,
                                            diagnostics,
                                            source=f"frame_{index}",
                                        )
                                        if frame_records:
                                            diagnostics.metadata["hit_frame_url"] = frame.url
                                            parsed = frame_records
                                            break
                                    except Exception as exc:
                                        diagnostics.warnings.append(f"SuccessFactors frame inspection failed for {frame.url}: {exc}")

                            for record in parsed:
                                href = record.discovered_url
                                if title := record.title_raw:
                                    diagnostics.counters["titles_seen"] = diagnostics.counters.get("titles_seen", 0) + 1
                                if href and href not in seen_urls:
                                    seen_urls.add(href)
                                    all_records.append(record)
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
