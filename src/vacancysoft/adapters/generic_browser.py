from __future__ import annotations

import asyncio
import logging
import time
import warnings
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# Suppress noisy TargetClosedError from Playwright cleanup
warnings.filterwarnings("ignore", message=".*Target page.*closed.*")
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from collections.abc import Callable

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    SourceAdapter,
)

# Type for the optional per-page callback.
# Called with (page_number, new_records_this_page, all_records_so_far).
PageCallback = Callable[[int, list[DiscoveredJobRecord], list[DiscoveredJobRecord]], None] | None
from vacancysoft.browser.session import browser_session

DEFAULT_PAGE_TIMEOUT_MS = 45_000
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
    "[class*='job'] a",
    "[class*='vacancy'] a",
    "[class*='opening'] a",
    "[class*='role'] a",
    "[class*='opportunit'] a",
    "[data-qa*='job'] a",
    "[data-test*='job'] a",
    "a[href*='opportunity']",
    "a[href*='careers/']",
    "a[data-id]",
    "article a",
    "li a",
    "tr a",
    ".search-results a",
    ".results-list a",
]

FALLBACK_LINK_SELECTOR = "a[href]"

# Heuristic selectors for location text within a job-card container.
# Kept deliberately conservative — we only accept short strings that look
# like real place names, not entire card descriptions.
#
# Many templates (ASP.NET "VSR" sites like 7IM, some iCIMS variants, etc.)
# encode "Location" in the id/aria-label/data-* attributes rather than the
# class name — those selectors below cover that case.
LOCATION_HINT_SELECTORS = (
    "[class*='location' i]",
    "[class*='Location']",
    "[class*='jobLocation' i]",
    "[class*='city' i]",
    "[class*='office' i]",
    "[class*='workplace' i]",
    "[data-ph-at-id*='location' i]",
    "[data-test*='location' i]",
    "[data-qa*='location' i]",
    "[id*='LocationID' i]",
    "[id*='jobLocation' i]",
    "[aria-label*='Location' i]",
    "[data-tooltip*='Location' i]",
    "[data-id*='LocationID' i]",
)

_LOCATION_REJECT_EXACT = {
    "location", "locations", "city", "office", "workplace",
    "location:", "city:", "office:",
    "remote", "hybrid", "on-site", "onsite",  # these belong to workplace_type
}
_LOCATION_MAX_LEN = 120

# When the matched element is a label+value container, the innerText looks
# like "Location London" / "City New York" / "Location:\nLondon" — strip the
# leading label so we store the actual place.
_LOCATION_LABEL_PREFIXES = (
    "location:", "location ", "location\n",
    "city:", "city ", "city\n",
    "office:", "office ", "office\n",
    "workplace:", "workplace ", "workplace\n",
)

NON_JOB_HREF_FRAGMENTS = (
    "/cookie",
    "/privacy",
    "/terms",
    "/faq",
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
    "vacancy-apply",
    ".html",
    "/details/",
    "jobreq=",
    "jobref=",
    "vacancyno=",
    "vacancyid=",
    "applyjob",
    "cw-careers/",
    "search-jobs/",
    "join-our-team/",
    "join-us/",
    "/listings/",
    "/roles/",
    "/recipe/",
    "folderdetail",
    "jobdetail_",
    "irecruit",
    "/career/",
    "/careers/",
    "/opportunit",
    "/ad/",
    "/job-offer/",
    "job-offer",
    "/apply/",
    "applytojob.com",
)

NON_JOB_TITLE_PREFIXES = (
    "home",
    "about",
    "contact",
    "apply now",
    "apply for",
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
    "skip to main",
    "skip to nav",
    "skip navigation",
    "vacancies",
    "our firm",
    "our people",
    "our team",
    "our story",
    "our values",
    "all opportunities",
    "all jobs",
    "view all",
    "saved jobs",
    "job seeker",
    "register",
    "sign in",
    "sign up",
    "create account",
    "forgot password",
    "privacy",
    "terms",
    "benefits",
    "locations",
    "early careers",
    "graduate",
    "internship",
    "explore",
    "who we are",
    "what we do",
    "why join",
    "join us",
    "job alerts",
    "search results",
    "more info",
    "jobs",
    "join talent",
    "talent pool",
    "subscribe",
    "privacy notice",
    "job applicant",
    "cookie",
    "accept",
    "decline",
    "back to",
    "next",
    "previous",
    "page",
    "show more",
    "load more",
    "clear all",
    "filter",
    "sort by",
    "refine",
    "vacancy search",
    "setup job alert",
    "job alert",
    "job field",
    "our history",
    "future talent",
    "browse job",
    "ma sélection",
    "job applicant",
    "new jobs",
    "new jobs (",
    "job opening",
    "menu",
    "search vacanc",
    "disclaimer",
    "your journey",
    "your career",
    "submit",
    "select",
    "reset",
    "close",
    "+",
    "−",
    "current vacanc",
    "working at",
    "see open",
    "open role",
    "open position",
    "consent",
    "our commitment",
    "our approach",
    "our impact",
    "our business",
    "our offices",
    "our locations",
    "introduction",
    "overview",
    "learn more",
    "increase text",
    "decrease text",
    "responsible investment",
    "home page",
    "main page",
    "back to home",
    "accessibility",
    "font size",
    "text size",
    "corporate service",
    "our ethos",
    "our culture",
    "our mission",
    "our vision",
    "our value",
    "list vacanc",
    "career field",
    "career area",
    "life at",
    "work at",
    "work for",
    "work with",
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


_NON_JOB_EXACT_TITLES = {
    "de", "fr", "en", "it", "es", "nl", "pt", "+", "-", "−",
    # Cities/locations that appear as link text but aren't job titles
    "new york", "london", "chicago", "boston", "san francisco", "los angeles",
    "toronto", "singapore", "hong kong", "dublin", "paris", "frankfurt",
    "amsterdam", "zurich", "sydney", "mumbai", "tokyo", "bermuda",
    "remote", "hybrid", "global", "worldwide", "all locations",
    "united states", "united kingdom", "usa", "uk", "us",
}

# Titles that look like category links: "IT ENGINEERING (13)", "NEW JOBS (558)"
_CATEGORY_LINK_RE = __import__("re").compile(r"^.{2,30}\s*\(\d+\)$")


def _looks_like_non_job_title(title: str) -> bool:
    lowered = title.lower().strip()
    if not lowered:
        return True
    if lowered in _NON_JOB_EXACT_TITLES:
        return True
    if len(lowered) < 3:
        return True
    if _CATEGORY_LINK_RE.match(title.strip()):
        return True
    return any(lowered.startswith(prefix) for prefix in NON_JOB_TITLE_PREFIXES)


async def _sniff_location(element: Any, title: str | None) -> str | None:
    """Look for a location-like sibling within a bounded ancestor of the link.

    Walks up to 3 ancestors so we're scoped to the job card, not the whole page.
    Skips text that is the same as the job title or matches a known non-location
    label (e.g. "Remote" belongs in workplace_type, not location).
    """
    title_lower = (title or "").strip().lower()
    try:
        # Collect up to 3 bounded ancestor handles.
        containers: list[Any] = []
        cursor = element
        for _ in range(3):
            try:
                parent = await cursor.evaluate_handle("node => node && node.parentElement")
            except Exception:
                break
            if not parent:
                break
            containers.append(parent)
            cursor = parent
    except Exception:
        return None

    for container in containers:
        for selector in LOCATION_HINT_SELECTORS:
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
            if not text or len(text) > _LOCATION_MAX_LEN:
                continue
            # Normalise: strip leading labels like "Location: London" or
            # "Location London" (id-matched containers often include the
            # aria-label text as a prefix).
            for prefix in _LOCATION_LABEL_PREFIXES:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            # Collapse whitespace (newlines between label and value)
            text = " ".join(text.split())
            if not text or len(text) > _LOCATION_MAX_LEN:
                continue
            lowered = text.lower().rstrip(":")
            if lowered in _LOCATION_REJECT_EXACT:
                continue
            if title_lower and lowered == title_lower:
                continue
            return text
    return None


def _looks_like_job_url(url: str) -> bool:
    lowered = url.lower()
    # Strip hash fragment for NON_JOB check — SPA routes like #/job/details/224
    # should not be blocked by the "#" non-job fragment
    url_without_hash = lowered.split("#")[0] if "#" in lowered else lowered
    if any(fragment in url_without_hash for fragment in NON_JOB_HREF_FRAGMENTS):
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

                # Fallback: if no href, check data-id and build URL from it
                if not href:
                    data_id = await element.get_attribute("data-id")
                    if data_id and title and not _looks_like_non_job_title(title):
                        # Build URL from data-id (Salesforce Lightning pattern)
                        href = f"details?jobReq={data_id}"
                    else:
                        continue

                url = _absolute_url(href, board_url)

                if not href:
                    continue
                if url.rstrip("/") == board_url.rstrip("/"):
                    continue  # Skip self-links back to the current page
                if not _same_domain(url, board_url):
                    continue
                if not _looks_like_job_url(url):
                    continue
                if title and _looks_like_non_job_title(title):
                    # Allow generic CTAs ("Learn more", "Apply now") if URL slug looks like a real job
                    title_lower = title.lower().strip()
                    is_generic_cta = title_lower in ("learn more", "apply now", "apply", "view", "view details", "read more", "find out more", "more info")
                    if is_generic_cta and _looks_like_job_url(url):
                        # Replace generic CTA with title extracted from URL slug
                        slug = url.rstrip("/").split("/")[-1]
                        title = slug.replace("-", " ").replace("_", " ").title()
                    else:
                        continue

                location = await _sniff_location(element, title)
                selector_results.append(
                    {
                        "title": title,
                        "url": url,
                        "href": href,
                        "selector": selector,
                        "location": location,
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
                if url.rstrip("/") == board_url.rstrip("/"):
                    continue  # Skip self-links back to the current page
                if not _same_domain(url, board_url):
                    continue
                if not _looks_like_job_url(url):
                    continue
                if title and _looks_like_non_job_title(title):
                    continue

                location = await _sniff_location(link, title)
                results.append(
                    {
                        "title": title,
                        "url": url,
                        "href": href,
                        "selector": "fallback",
                        "location": location,
                    }
                )
            except Exception:
                diagnostics.counters["listing_parse_failures"] = diagnostics.counters.get("listing_parse_failures", 0) + 1

        if results:
            selector_used = "fallback"

    # ── Iframe fallback: scan child frames for job links ──
    if not results:
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    frame_links = await frame.query_selector_all("a[href]")
                    for link in frame_links:
                        try:
                            title = _clean(await link.inner_text())
                            href = await link.get_attribute("href")
                            if not href or href.startswith("#") or href.startswith("mailto:"):
                                continue
                            url = _absolute_url(href, board_url)
                            if not _looks_like_job_url(url):
                                continue
                            if title and _looks_like_non_job_title(title):
                                is_generic_cta = title.lower().strip() in ("learn more", "apply now", "apply", "view", "view details", "read more", "find out more", "more info")
                                if is_generic_cta and _looks_like_job_url(url):
                                    slug = url.rstrip("/").split("/")[-1]
                                    title = slug.replace("-", " ").replace("_", " ").title()
                                else:
                                    continue
                            location = await _sniff_location(link, title)
                            results.append({
                                "title": title,
                                "url": url,
                                "href": href,
                                "selector": "iframe",
                                "location": location,
                            })
                        except Exception:
                            pass
                    if results:
                        selector_used = "iframe"
                        diagnostics.metadata["iframe_source"] = frame.url[:100]
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if selector_used:
        diagnostics.metadata["last_selector_used"] = selector_used
    diagnostics.counters["listings_seen"] = diagnostics.counters.get("listings_seen", 0) + len(results)
    return results


_NEXT_BUTTON_SELECTORS = [
    "a.next:not(.disabled)",
    "a[aria-label*='next' i]:not([aria-disabled='true'])",
    "button[aria-label*='next' i]:not([aria-disabled='true']):not(:disabled)",
    "a.pagination-next:not(.disabled)",
    "[class*='pagination'] a:last-child:not(.disabled)",
    "a:has-text('Next'):not(.disabled)",
    "a:has-text('›')",
    "a:has-text('»')",
]


class GenericBrowserAdapter(SourceAdapter):
    adapter_name = "generic_site"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=True,
        supports_incremental_sync=False,
        supports_api=False,
        supports_html=False,
        supports_browser=True,
        supports_site_rescue=True,
    )

    async def _discover_with_context(
        self,
        context: Any,
        source_config: dict[str, Any],
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        """Core scraping logic using an existing browser context."""
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        company = str(source_config.get("company") or board_url).strip()
        timeout_ms = int(source_config.get("page_timeout_ms", DEFAULT_PAGE_TIMEOUT_MS))
        wait_ms = int(source_config.get("wait_after_nav_ms", DEFAULT_WAIT_AFTER_NAV_MS))
        max_pages = int(source_config.get("max_pages", 999))
        since = source_config.get("_since")

        diagnostics = AdapterDiagnostics(
            metadata={
                "board_url": board_url,
                "company": company,
                "page_timeout_ms": timeout_ms,
                "wait_after_nav_ms": wait_ms,
                "max_pages": max_pages,
                "since": since.isoformat() if since else None,
                "mode": "vacancy_url_harvest",
            }
        )

        seen_urls: set[str] = set()
        all_records: list[DiscoveredJobRecord] = []
        started = time.perf_counter()

        page = await context.new_page()
        self._firefox_context = None  # track for cleanup
        try:
            response = await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(wait_ms)

            diagnostics.metadata["final_url"] = page.url
            if response is not None:
                diagnostics.metadata["http_status"] = response.status

            # Auto-detect Cloudflare challenge and retry with Firefox
            page_title = await page.title()
            if any(t in page_title.lower() for t in ("just a moment", "attention required", "checking your browser")):
                diagnostics.warnings.append(f"Cloudflare detected (title='{page_title[:40]}'), retrying with Firefox")
                await page.close()
                pw_instance = context.browser._playwright  # type: ignore
                ff_browser = await pw_instance.firefox.launch(headless=True)
                ff_ctx = await ff_browser.new_context(viewport={"width": 1280, "height": 900})
                self._firefox_context = (ff_browser, ff_ctx)
                page = await ff_ctx.new_page()
                context = ff_ctx
                response = await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(wait_ms)
                diagnostics.metadata["browser"] = "firefox"
                diagnostics.metadata["final_url"] = page.url
                if response is not None:
                    diagnostics.metadata["http_status"] = response.status

            # Dismiss cookie/alert overlays
            await page.evaluate("""() => {
                document.querySelectorAll(
                    '#system-ialert, [class*="ialert"], [class*="cookie-banner"], [class*="cookie-consent"], [class*="consent-banner"]'
                ).forEach(el => el.style.display = 'none');
            }""")

            # Scroll to load lazy content
            scroll_rounds = int(source_config.get("scroll_rounds", 3))
            for _scroll in range(scroll_rounds):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(600)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)

            pages_scraped = 0
            import sys

            while pages_scraped < max_pages:
                candidates = await _collect_candidate_urls(page, board_url, diagnostics)

                # If first page finds nothing, wait for AJAX and retry once
                if pages_scraped == 0 and not candidates:
                    await page.wait_for_timeout(5000)
                    for _ in range(3):
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await page.wait_for_timeout(800)
                    candidates = await _collect_candidate_urls(page, board_url, diagnostics)
                    if candidates:
                        diagnostics.metadata["ajax_retry"] = True

                new_on_page = 0
                page_records: list[DiscoveredJobRecord] = []
                for candidate in candidates:
                    url = str(candidate["url"])
                    if url in seen_urls:
                        diagnostics.counters["duplicate_urls"] = diagnostics.counters.get("duplicate_urls", 0) + 1
                        continue
                    seen_urls.add(url)
                    new_on_page += 1
                    location = _clean(candidate.get("location"))
                    if location:
                        diagnostics.counters["locations_sniffed"] = (
                            diagnostics.counters.get("locations_sniffed", 0) + 1
                        )
                    record = DiscoveredJobRecord(
                        external_job_id=url,
                        title_raw=_clean(candidate["title"]),
                        location_raw=location,
                        posted_at_raw=None,
                        summary_raw=None,
                        discovered_url=url,
                        apply_url=url,
                        listing_payload={
                            "strategy": "candidate_url_harvest",
                            "href": candidate["href"],
                            "selector": candidate["selector"],
                            "location": location,
                            "page_number": pages_scraped + 1,
                        },
                        completeness_score=0.55 if location else 0.50,
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
                    page_records.append(record)
                    all_records.append(record)

                pages_scraped += 1
                diagnostics.counters["pages_scraped"] = pages_scraped
                print(f"\r  [{company}] page {pages_scraped}: +{new_on_page} new, {len(all_records)} total", end="", flush=True, file=sys.stderr)

                if on_page_scraped and page_records:
                    try:
                        on_page_scraped(pages_scraped, page_records, all_records)
                    except Exception:
                        pass

                if new_on_page == 0:
                    break
                if pages_scraped >= max_pages:
                    break

                advanced = await self._advance_to_next_page(page, wait_ms)
                if not advanced:
                    break

        finally:
            await page.close()
            # Clean up Firefox if it was auto-launched for Cloudflare bypass
            if self._firefox_context:
                ff_browser, ff_ctx = self._firefox_context
                await ff_ctx.close()
                await ff_browser.close()
                self._firefox_context = None

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        if all_records:
            print(f"\r  [{company}] done: {len(all_records)} jobs from {diagnostics.counters.get('pages_scraped', 1)} pages in {diagnostics.timings_ms['discover']/1000:.0f}s", file=sys.stderr)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("GenericBrowserAdapter requires job_board_url")

        if since is not None:
            source_config = dict(source_config)
            source_config["_since"] = since

        # Check if a shared browser context was passed (batch mode)
        shared_context = source_config.get("_browser_context")
        if shared_context:
            return await self._discover_with_context(shared_context, source_config, on_page_scraped)

        # Single-board mode: launch own Playwright instance
        use_firefox = source_config.get("use_firefox", False)
        try:
            async with async_playwright() as playwright:
                if use_firefox:
                    browser = await playwright.firefox.launch(headless=True)
                    context = await browser.new_context(viewport={"width": 1280, "height": 900})
                    try:
                        result = await self._discover_with_context(context, source_config, on_page_scraped)
                    finally:
                        await context.close()
                        await browser.close()
                    return result
                else:
                    async with browser_session(playwright, headless=True) as (_browser, context):
                        return await self._discover_with_context(context, source_config, on_page_scraped)
        except PlaywrightTimeoutError as exc:
            raise
        except PlaywrightError as exc:
            raise

    async def discover_batch(
        self,
        configs: list[dict[str, Any]],
        batch_size: int = 15,
        on_board_complete: Any = None,
    ) -> list[tuple[dict, DiscoveryPage | None, Exception | None]]:
        """Discover multiple boards sharing a Playwright process in batches.

        Launches one Playwright process per batch_size boards. Each board gets
        a fresh browser context (clean cookies/cache) but shares the Chromium
        process — avoids the 1-2s process launch overhead per board.

        Args:
            configs: list of source_config dicts (must include job_board_url, company)
            batch_size: boards per Playwright process (default 15)
            on_board_complete: optional callback(config, page_or_none, error_or_none)

        Returns:
            list of (config, DiscoveryPage | None, Exception | None) tuples
        """
        from vacancysoft.browser.session import _DEFAULT_USER_AGENT

        results: list[tuple[dict, DiscoveryPage | None, Exception | None]] = []

        for batch_start in range(0, len(configs), batch_size):
            batch = configs[batch_start:batch_start + batch_size]

            try:
                async with async_playwright() as playwright:
                    chromium = await playwright.chromium.launch(headless=True)
                    firefox = None  # lazy-launch only if needed
                    try:
                        for config in batch:
                            use_firefox = config.get("use_firefox", False)
                            if use_firefox:
                                if firefox is None:
                                    firefox = await playwright.firefox.launch(headless=True)
                                browser = firefox
                            else:
                                browser = chromium

                            context = await browser.new_context(
                                user_agent=_DEFAULT_USER_AGENT if not use_firefox else None,
                                viewport={"width": 1280, "height": 900},
                            )
                            try:
                                page_result = await self._discover_with_context(
                                    context, config, config.get("_on_page_scraped"),
                                )
                                results.append((config, page_result, None))
                                if on_board_complete:
                                    on_board_complete(config, page_result, None)
                            except Exception as exc:
                                results.append((config, None, exc))
                                if on_board_complete:
                                    on_board_complete(config, None, exc)
                            finally:
                                await context.close()
                    finally:
                        await chromium.close()
                        if firefox:
                            await firefox.close()
            except Exception as exc:
                # Playwright itself failed — mark remaining batch as failed
                remaining = batch_size - (len(results) - batch_start)
                for config in batch[len(results) - batch_start:]:
                    results.append((config, None, exc))
                    if on_board_complete:
                        on_board_complete(config, None, exc)

        return results

    @staticmethod
    async def _advance_to_next_page(page: Any, wait_ms: int) -> bool:
        """Click the 'Next' button to load the next page of results.

        Works for both traditional link-based pagination and AJAX-driven
        sites where clicking Next replaces the results in-place.
        Returns True if we successfully advanced.
        """
        for selector in _NEXT_BUTTON_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if not el:
                    continue

                # Check it's not disabled
                disabled = await el.get_attribute("aria-disabled")
                if disabled == "true":
                    continue
                cls = await el.get_attribute("class") or ""
                if "disabled" in cls.lower():
                    continue

                # Use force click via JS to bypass any remaining overlays
                await page.evaluate("(el) => el.click()", el)

                # Wait for AJAX content to refresh
                await page.wait_for_timeout(2000)
                return True

            except Exception:
                continue

        return False