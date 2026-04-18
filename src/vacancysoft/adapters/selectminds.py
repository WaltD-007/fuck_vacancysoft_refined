from __future__ import annotations

import re
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
from vacancysoft.browser import browser_session
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

PAGE_TIMEOUT_MS = 45_000

# SelectMinds/Oracle Taleo Sourcing uses server-rendered HTML
JOB_LINK_SELECTORS = [
    "a[href*='/jobs/']",
    "a[href*='/job/']",
    "a[class*='job']",
    "a[class*='Job']",
    "[class*='job_list'] a",
    "[class*='job-list'] a",
    "[class*='search-result'] a",
    "table.job_list_table a",
    ".job_link",
    "a.job_link",
    "td.job_list_row a",
]

_REJECT_TITLES = {
    "search", "apply", "learn more", "skip to main content",
    "sign in", "log in", "register", "privacy", "cookie",
    "back to top", "next", "previous", "home",
}

_REJECT_URL_TOKENS = (
    "/login", "/register", "/privacy", "/cookie",
    "/faq", "/help", "/about", "/contact",
    "javascript:", "mailto:", "#",
)

# Sibling/descendant selectors commonly used by SelectMinds / Oracle Taleo Sourcing
# templates to render location next to the job title.
_LOCATION_HINT_SELECTORS = (
    "[class*='location' i]",
    "[class*='Location']",
    "[class*='jobLocation' i]",
    "[class*='city' i]",
    "[data-ph-at-id*='location' i]",
    "span.location",
    "td.job_list_row_location",
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_valid_job_link(href: str) -> bool:
    if not href:
        return False
    lower = href.lower()
    if any(lower.startswith(tok) for tok in ("#", "javascript:", "mailto:")):
        return False
    if any(tok in lower for tok in _REJECT_URL_TOKENS):
        return False
    return True


def _make_record(
    title: str | None,
    href: str,
    company_name: str,
    board_url: str,
    source_label: str,
    location: str | None = None,
) -> DiscoveredJobRecord:
    completeness = 0.5 if href else 0.25
    if title:
        completeness += 0.25
    if location:
        completeness += 0.15
    return DiscoveredJobRecord(
        external_job_id=href or title or "unknown",
        title_raw=title,
        location_raw=location,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload={"href": href, "title": title, "source": source_label, "location": location},
        completeness_score=round(min(completeness, 1.0), 4),
        extraction_confidence=0.68,
        provenance={
            "adapter": "selectminds",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name,
            "platform": "SelectMinds",
            "board_url": board_url,
            "source": source_label,
        },
    )


async def _extract_location_near(el: Any) -> str | None:
    """Walk up to two ancestors looking for a sibling with a location-like class.

    SelectMinds / Taleo Sourcing usually renders location as a sibling span
    or in an adjacent table cell. We bound the walk at depth 2 so we don't
    vacuum in text from an unrelated card.
    """
    containers: list[Any] = []
    try:
        parent = await el.evaluate_handle("node => node.parentElement")
        if parent:
            containers.append(parent)
            grandparent = await parent.evaluate_handle("node => node && node.parentElement")
            if grandparent:
                containers.append(grandparent)
    except Exception:
        return None

    for container in containers:
        for selector in _LOCATION_HINT_SELECTORS:
            try:
                found = await container.query_selector(selector)
            except Exception:
                continue
            if not found:
                continue
            try:
                text = (await found.inner_text()).strip()
            except Exception:
                continue
            if not text or len(text) > 120:
                continue
            # Reject obvious non-locations (labels like "Location:")
            if text.lower().rstrip(":") in {"location", "city", "office"}:
                continue
            return text
    return None


class SelectMindsAdapter(SourceAdapter):
    adapter_name = "selectminds"
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
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or "").strip()
        if not board_url:
            raise ValueError("SelectMinds source_config requires job_board_url")

        company_name = lookup_company(
            "selectminds",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )
        timeout_ms = int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS))
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        t0 = time.monotonic()

        records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()

        async with async_playwright() as pw:
            async with browser_session(pw) as (_browser, ctx):
                page = await ctx.new_page()

                try:
                    resp = await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    diagnostics.counters["http_status"] = resp.status if resp else 0
                    await page.wait_for_timeout(3000)
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    diagnostics.errors.append(f"Navigation failed: {exc}")

                # Scroll to load lazy content
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await page.wait_for_timeout(800)

                # Try each selector to find job links
                for selector in JOB_LINK_SELECTORS:
                    try:
                        elements = await page.query_selector_all(selector)
                        for el in elements:
                            href = await el.get_attribute("href")
                            if not href:
                                continue
                            href = urljoin(board_url, href)
                            if href in seen_urls:
                                continue
                            if not _is_valid_job_link(href):
                                continue
                            title = None
                            try:
                                title = (await el.inner_text()).strip()
                            except Exception:
                                pass
                            if title and title.lower() in _REJECT_TITLES:
                                continue
                            if title and len(title) < 3:
                                continue
                            seen_urls.add(href)
                            location = await _extract_location_near(el)
                            if location:
                                diagnostics.counters["locations_found"] = (
                                    diagnostics.counters.get("locations_found", 0) + 1
                                )
                            records.append(
                                _make_record(
                                    title,
                                    href,
                                    company_name,
                                    board_url,
                                    f"dom:{selector}",
                                    location=location,
                                )
                            )
                    except (PlaywrightError, PlaywrightTimeoutError):
                        continue

                diagnostics.counters["selectors_tried"] = len(JOB_LINK_SELECTORS)
                diagnostics.counters["total_jobs"] = len(records)
                await page.close()

        diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)

        if on_page_scraped:
            await on_page_scraped(1, records, records)

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
