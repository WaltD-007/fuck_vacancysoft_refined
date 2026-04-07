from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

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
from vacancysoft.browser.session import browser_session

DEFAULT_PAGE_TIMEOUT_MS = 20_000
DEFAULT_WAIT_AFTER_NAV_MS = 2_000

CANDIDATE_LINK_SELECTORS = [
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
    "[class*='job-card'] a",
    "[class*='job-item'] a",
    "[class*='job-listing'] a",
    "[class*='vacancy'] a",
    "[class*='opening'] a",
    "[class*='role'] a",
    "[class*='opportunit'] a",
    "[data-qa*='job'] a",
    "[data-test*='job'] a",
    "article a",
    "li a",
    "tr a",
    ".search-results a",
    ".results-list a",
]

FALLBACK_LINK_SELECTOR = "a[href]"

NON_JOB_HREF_FRAGMENTS = (
    "/cookie",
    "/privacy",
    "/terms",
    "/faq",
    "/about",
    "/contact",
    "/culture",
    "/benefits",
    "/team",
    "/teams",
    "/departments",
    "/talent-community",
    "/job-alert",
    "/login",
    "/account",
    "/working-for-us",
    "/discover",
    "/key-teams",
    "/early-careers",
    "/graduates",
    "/internships",
    "/students",
    "/leadership",
    "/board-of-directors",
    "/work-at",
    "/life-at",
    "/what-we-can-offer",
    "javascript:",
    "mailto:",
    "tel:",
    "#",
)

JOBISH_HREF_FRAGMENTS = (
    "/job",
    "jobdetail",
    "job-detail",
    "job_detail",
    "/jobs/",
    "/vacanc",
    "/position",
    "/posting",
    "/requisition",
    "/opening",
    "vacancydetails",
    "jobid=",
    "reqid=",
    "requisitionid",
)

NON_JOB_TITLE_PREFIXES = (
    "home",
    "about",
    "contact",
    "apply",
    "read more",
    "discover",
    "key teams",
    "careers",
    "job opportunities",
    "search jobs",
    "latest job vacancies",
    "working for us",
    "cookie policy",
    "your account",
    "talent community",
    "departments",
    "find out more",
    "back to the main site",
    "culture",
    "culture here",
    "log in",
    "login",
    "english",
    "deutsch",
    "skip to content",
)


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


def _same_domain(url: str, board_url: str) -> bool:
    try:
        return urlparse(url).netloc.lower() == urlparse(board_url).netloc.lower()
    except Exception:
        return False


def _looks_like_non_job_title(title: str) -> bool:
    lowered = title.lower().strip()
    return any(lowered.startswith(prefix) for prefix in NON_JOB_TITLE_PREFIXES)


def _looks_like_job_url(url: str) -> bool:
    lowered = url.lower()
    if any(fragment in lowered for fragment in NON_JOB_HREF_FRAGMENTS):
        return False
    return any(token in lowered for token in JOBISH_HREF_FRAGMENTS)


async def _collect_candidate_urls(page: Any, board_url: str, diagnostics: AdapterDiagnostics) -> list[dict[str, str | None]]:
    results: list[dict[str, str | None]] = []
    selector_used: str | None = None

    for selector in CANDIDATE_LINK_SELECTORS:
        try:
            elements = await page.query_selector_all(selector)
        except Exception:
            continue
        if not elements:
            continue

        selector_results: list[dict[str, str | None]] = []
        for element in elements:
            try:
                title = _clean(await element.inner_text())
                href = await element.get_attribute("href")
                url = _absolute_url(href, board_url)

                if not href:
                    continue
                if not _same_domain(url, board_url):
                    continue
                if not _looks_like_job_url(url):
                    continue
                if title and _looks_like_non_job_title(title):
                    continue

                selector_results.append(
                    {
                        "title": title,
                        "url": url,
                        "href": href,
                        "selector": selector,
                    }
                )
            except Exception:
                diagnostics.counters["listing_parse_failures"] = diagnostics.counters.get("listing_parse_failures", 0) + 1

        if selector_results:
            results.extend(selector_results)
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

                if not href:
                    continue
                if not _same_domain(url, board_url):
                    continue
                if not _looks_like_job_url(url):
                    continue
                if title and _looks_like_non_job_title(title):
                    continue

                results.append(
                    {
                        "title": title,
                        "url": url,
                        "href": href,
                        "selector": "fallback",
                    }
                )
            except Exception:
                diagnostics.counters["listing_parse_failures"] = diagnostics.counters.get("listing_parse_failures", 0) + 1

        if results:
            selector_used = "fallback"

    if selector_used:
        diagnostics.metadata["last_selector_used"] = selector_used
    diagnostics.counters["listings_seen"] = diagnostics.counters.get("listings_seen", 0) + len(results)
    return results


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
        timeout_ms = int(source_config.get("page_timeout_ms", DEFAULT_PAGE_TIMEOUT_MS))
        wait_ms = int(source_config.get("wait_after_nav_ms", DEFAULT_WAIT_AFTER_NAV_MS))

        diagnostics = AdapterDiagnostics(
            metadata={
                "board_url": board_url,
                "company": company,
                "page_timeout_ms": timeout_ms,
                "wait_after_nav_ms": wait_ms,
                "since": since.isoformat() if since else None,
                "cursor_ignored": cursor is not None,
                "mode": "vacancy_url_harvest",
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
                async with browser_session(playwright, headless=True) as (_browser, context):
                    page = await context.new_page()
                    try:
                        response = await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        await page.wait_for_timeout(wait_ms)

                        diagnostics.metadata["final_url"] = page.url
                        if response is not None:
                            diagnostics.metadata["http_status"] = response.status

                        candidates = await _collect_candidate_urls(page, board_url, diagnostics)

                        for candidate in candidates:
                            url = str(candidate["url"])
                            if url in seen_urls:
                                diagnostics.counters["duplicate_urls"] = diagnostics.counters.get("duplicate_urls", 0) + 1
                                continue
                            seen_urls.add(url)
                            all_records.append(
                                DiscoveredJobRecord(
                                    external_job_id=url,
                                    title_raw=_clean(candidate["title"]),
                                    location_raw=None,
                                    posted_at_raw=None,
                                    summary_raw=None,
                                    discovered_url=url,
                                    apply_url=url,
                                    listing_payload={
                                        "strategy": "candidate_url_harvest",
                                        "href": candidate["href"],
                                        "selector": candidate["selector"],
                                    },
                                    completeness_score=0.50,
                                    extraction_confidence=0.60,
                                    provenance={
                                        "adapter": "generic_site",
                                        "method": ExtractionMethod.BROWSER.value,
                                        "company": company,
                                        "platform": "Generic Browser",
                                        "board_url": board_url,
                                        "strategy": "candidate_url_harvest",
                                    },
                                )
                            )
                    finally:
                        await page.close()
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