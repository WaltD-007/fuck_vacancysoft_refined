from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]
DEFAULT_PAGE_TIMEOUT_MS = 20_000
DEFAULT_WAIT_AFTER_NAV_MS = 2_000
SEARCH_PARAMS = ["q", "query", "keyword", "search", "keywords", "term", "s"]
TITLE_LINK_SELECTORS = [
    "a[data-ph-at-id='job-title-link']",
    "a.job-title",
    ".job-title a",
    "h2.title a",
    "h3.title a",
    "[class*='jobTitle'] a",
    "[class*='job-title'] a",
    "[class*='position-title'] a",
    "[class*='vacancy-title'] a",
    "[class*='career-title'] a",
    "article h2 a",
    "article h3 a",
    "li.job a",
    ".job-card a",
    ".job-item a",
    ".job-listing a",
    "table tr td a[href*='job']",
    "table tr td a[href*='vacanc']",
    "table tr td a[href*='career']",
    ".search-results a",
    ".results-list a",
]
SEARCH_INPUT_SELECTORS = [
    "input[id*='keyword' i]",
    "input[name*='keyword' i]",
    "input[placeholder*='search' i]",
    "input[placeholder*='job title' i]",
    "input[placeholder*='role' i]",
    "input[type='search']",
    "#keywordInput",
    "#searchInput",
    ".search-input input",
]
FALLBACK_LINK_SELECTOR = "a[href*='job'], a[href*='vacanc'], a[href*='career'], a[href*='position']"
SKIP_PREFIXES = ("home", "about", "contact", "apply")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _absolute_url(href: str | None, base_url: str) -> str:
    if not href:
        return base_url
    if href.startswith("http"):
        return href
    return urljoin(base_url, href)


def _with_query_param(board_url: str, param: str, term: str) -> str:
    parsed = urlparse(board_url)
    query = {k: values[-1] for k, values in parse_qs(parsed.query).items() if values}
    query[param] = term
    return parsed._replace(query=urlencode(query)).geturl()


async def _first_visible_input(page: Any) -> Any | None:
    for selector in SEARCH_INPUT_SELECTORS:
        try:
            handle = await page.query_selector(selector)
            if handle and await handle.is_visible():
                return handle
        except Exception:
            continue
    return None


async def _collect_jobs_on_page(page: Any, board_url: str, diagnostics: AdapterDiagnostics) -> list[dict[str, str | None]]:
    results: list[dict[str, str | None]] = []
    selector_used: str | None = None

    for selector in TITLE_LINK_SELECTORS:
        try:
            elements = await page.query_selector_all(selector)
        except Exception:
            continue
        if not elements:
            continue
        for element in elements:
            try:
                title = _clean(await element.inner_text())
                href = await element.get_attribute("href")
                url = _absolute_url(href, board_url)
                if title and len(title) > 3:
                    results.append({"title": title, "url": url, "href": href, "selector": selector})
            except Exception:
                diagnostics.counters["listing_parse_failures"] = diagnostics.counters.get("listing_parse_failures", 0) + 1
        if results:
            selector_used = selector
            break

    if not results:
        try:
            links = await page.query_selector_all(FALLBACK_LINK_SELECTOR)
        except Exception:
            links = []
        for link in links:
            try:
                title = _clean(await link.inner_text())
                href = await link.get_attribute("href")
                url = _absolute_url(href, board_url)
                lowered = title.lower() if title else ""
                if title and len(title) > 5 and not lowered.startswith(SKIP_PREFIXES):
                    results.append({"title": title, "url": url, "href": href, "selector": "fallback"})
            except Exception:
                diagnostics.counters["listing_parse_failures"] = diagnostics.counters.get("listing_parse_failures", 0) + 1
        if results:
            selector_used = "fallback"

    if selector_used:
        diagnostics.metadata["last_selector_used"] = selector_used
    diagnostics.counters["listings_seen"] = diagnostics.counters.get("listings_seen", 0) + len(results)
    return results


async def _try_search_on_page(page: Any, term: str, wait_ms: int, diagnostics: AdapterDiagnostics) -> bool:
    input_handle = await _first_visible_input(page)
    if not input_handle:
        return False
    try:
        await input_handle.click()
        try:
            await input_handle.press("Meta+A")
        except Exception:
            pass
        await input_handle.fill(term)
        await input_handle.press("Enter")
        await page.wait_for_timeout(wait_ms)
        diagnostics.counters["on_page_searches"] = diagnostics.counters.get("on_page_searches", 0) + 1
        return True
    except Exception as exc:
        diagnostics.warnings.append(f"Generic browser on-page search failed for term '{term}': {exc}")
        return False


class GenericBrowserAdapter(SourceAdapter):
    adapter_name = "generic_site"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=False,
        supports_html=False,
        supports_browser=True,
        supports_site_rescue=True,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("GenericBrowserAdapter requires job_board_url")

        company = str(source_config.get("company") or board_url).strip()
        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        search_params = [str(param).strip() for param in (source_config.get("search_params") or SEARCH_PARAMS) if str(param).strip()]
        timeout_ms = int(source_config.get("page_timeout_ms", DEFAULT_PAGE_TIMEOUT_MS))
        wait_ms = int(source_config.get("wait_after_nav_ms", DEFAULT_WAIT_AFTER_NAV_MS))
        diagnostics = AdapterDiagnostics(
            metadata={
                "board_url": board_url,
                "company": company,
                "search_terms": search_terms,
                "search_params": search_params,
                "page_timeout_ms": timeout_ms,
                "wait_after_nav_ms": wait_ms,
                "since": since.isoformat() if since else None,
                "cursor_ignored": cursor is not None,
            }
        )
        if cursor is not None:
            diagnostics.warnings.append("GenericBrowserAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("GenericBrowserAdapter cannot enforce incremental sync. since was recorded but not applied.")

        seen_urls: set[str] = set()
        all_records: list[DiscoveredJobRecord] = []
        started = time.perf_counter()

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    for index, term in enumerate(search_terms):
                        url_search_succeeded = False
                        for param in search_params:
                            test_url = _with_query_param(board_url, param, term)
                            diagnostics.counters["url_search_attempts"] = diagnostics.counters.get("url_search_attempts", 0) + 1
                            try:
                                await page.goto(test_url, wait_until="domcontentloaded", timeout=timeout_ms)
                                await page.wait_for_timeout(wait_ms)
                                jobs = await _collect_jobs_on_page(page, board_url, diagnostics)
                            except Exception:
                                jobs = []
                            if not jobs:
                                continue
                            url_search_succeeded = True
                            diagnostics.counters["url_search_hits"] = diagnostics.counters.get("url_search_hits", 0) + 1
                            for job in jobs:
                                url = str(job["url"])
                                if url in seen_urls:
                                    diagnostics.counters["duplicate_urls"] = diagnostics.counters.get("duplicate_urls", 0) + 1
                                    continue
                                seen_urls.add(url)
                                all_records.append(
                                    DiscoveredJobRecord(
                                        external_job_id=url,
                                        title_raw=_clean(job["title"]),
                                        location_raw=None,
                                        posted_at_raw=None,
                                        summary_raw=None,
                                        discovered_url=url,
                                        apply_url=url,
                                        listing_payload={
                                            "search_term": term,
                                            "strategy": "url_param",
                                            "search_param": param,
                                            "href": job["href"],
                                            "selector": job["selector"],
                                        },
                                        completeness_score=0.6667,
                                        extraction_confidence=0.72,
                                        provenance={
                                            "adapter": "generic_site",
                                            "method": ExtractionMethod.BROWSER.value,
                                            "company": company,
                                            "platform": "Generic Browser",
                                            "board_url": board_url,
                                            "strategy": "url_param",
                                            "search_term": term,
                                            "search_param": param,
                                        },
                                    )
                                )
                            break

                        if url_search_succeeded:
                            continue

                        await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        await page.wait_for_timeout(wait_ms)
                        searched = await _try_search_on_page(page, term, wait_ms, diagnostics)
                        if not searched and index > 0:
                            diagnostics.counters["terms_skipped_without_search"] = diagnostics.counters.get(
                                "terms_skipped_without_search", 0
                            ) + 1
                            continue
                        jobs = await _collect_jobs_on_page(page, board_url, diagnostics)
                        strategy = "on_page_search" if searched else "extract_all"
                        confidence = 0.7 if searched else 0.62
                        for job in jobs:
                            url = str(job["url"])
                            if url in seen_urls:
                                diagnostics.counters["duplicate_urls"] = diagnostics.counters.get("duplicate_urls", 0) + 1
                                continue
                            seen_urls.add(url)
                            all_records.append(
                                DiscoveredJobRecord(
                                    external_job_id=url,
                                    title_raw=_clean(job["title"]),
                                    location_raw=None,
                                    posted_at_raw=None,
                                    summary_raw=None,
                                    discovered_url=url,
                                    apply_url=url,
                                    listing_payload={
                                        "search_term": term,
                                        "strategy": strategy,
                                        "href": job["href"],
                                        "selector": job["selector"],
                                    },
                                    completeness_score=0.6667,
                                    extraction_confidence=confidence,
                                    provenance={
                                        "adapter": "generic_site",
                                        "method": ExtractionMethod.BROWSER.value,
                                        "company": company,
                                        "platform": "Generic Browser",
                                        "board_url": board_url,
                                        "strategy": strategy,
                                        "search_term": term,
                                    },
                                )
                            )
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Generic browser timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Generic browser failure: {exc}")
            raise

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
