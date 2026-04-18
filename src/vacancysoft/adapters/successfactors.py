from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "audit", "cyber", "legal"]
PAGE_TIMEOUT_MS = 60_000
JOB_SELECTORS = [
    "a[href*='/job/']",
    "a[href*='/jobs/']",
    "a[href*='job?']",
    "[class*='job-result'] a",
    "[class*='search-results'] a",
    "[class*='opening'] a",
    "[class*='vacancy'] a",
    "[class*='jobTitle']",
    ".jobResultItem",
    ".JobResultItem",
    "a[href*='job_req_id']",
    ".position-title",
    "table a[href*='job']",
]
SEARCH_INPUT_SELECTOR = (
    "input#keywordInput, input[name*='keyword' i], input[id*='keyword' i], "
    "input[placeholder*='search' i], input[placeholder*='title' i]"
)
SEARCH_BUTTON_SELECTOR = (
    "button[type='submit'], button[aria-label*='search' i], button[title*='search' i], "
    "button[id*='search' i], input[type='submit'], a[role='button']"
)
NETWORK_HINTS = ("job", "search", "career", "odata", "candidate", "posting", "requisition")
_REJECT_TITLE_TOKENS = {
    "find out more",
    "search jobs",
    "about us",
    "our benefits",
    "our business areas",
    "our offices",
    "early careers",
    "candidate login",
    "colleague login",
    "cookie policy",
}
_REJECT_URL_TOKENS = (
    "/content/",
    "cookie",
    "benefits",
    "business-areas",
    "offices-and-locations",
    "early-careers",
    "candidate-login",
    "colleague-login",
)

# DOM selectors commonly used by SuccessFactors templates for per-row location text.
_LOCATION_HINT_SELECTORS = (
    "[class*='location' i]",
    "[class*='jobLocation' i]",
    "[class*='Location']",
    "[class*='city' i]",
    "[data-test*='location' i]",
    "span.location",
)

# JSON keys across the many SuccessFactors/SAP variants that carry location info.
_JSON_LOCATION_KEYS = (
    "jobLocation",
    "location",
    "locationDescription",
    "cityState",
    "city",
    "state",
    "country",
    "countryName",
    "locationCity",
    "locationCountry",
    "workLocation",
    "primaryLocation",
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


def _looks_like_job_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    if any(token in lowered for token in _REJECT_URL_TOKENS):
        return False
    return any(token in lowered for token in ("/job/", "/jobs/", "job?", "jobid", "job_req_id", "requisition"))


def _looks_like_job_title(title: str | None) -> bool:
    if not title:
        return False
    lowered = title.strip().lower()
    if lowered in _REJECT_TITLE_TOKENS:
        return False
    if lowered.startswith("find out more"):
        return False
    return len(lowered) >= 4


# Many SF-fronted career sites (e.g. careers.mizuhoemea.com) render URLs like
#   /job/{Location}-{Title…}-{LocShort}/{id}/
# where {LocShort} is a truncation of {Location} (e.g. "London"/"Lond"). The
# truncation check is the strong signal — it means we only accept a prefix
# token that's corroborated by a matching suffix token, avoiding false
# positives on titles that start with a capitalised non-location word.
_URL_LOCATION_RE = re.compile(
    r"/job/"
    r"([A-Za-z][A-Za-z]+)"   # prefix token (location candidate)
    r"-.+?-"                 # anything in between
    r"([A-Za-z][A-Za-z]+)"   # suffix token (truncated location candidate)
    r"/\d+/?(?:[?#].*)?$"
)


def _extract_location_from_url(url: str | None) -> str | None:
    """Parse a ``/job/{Location}-{…}-{LocShort}/{id}/`` style URL.

    Returns the prefix token only when the suffix token is a case-insensitive
    truncation of the prefix (length ≥ 3) — this disambiguates real locations
    from job titles that happen to start with a capitalised word.
    """
    if not url:
        return None
    try:
        decoded = unquote(url)
    except Exception:
        decoded = url
    match = _URL_LOCATION_RE.search(decoded)
    if not match:
        return None
    prefix, suffix = match.group(1), match.group(2)
    if len(suffix) < 3 or len(prefix) < len(suffix):
        return None
    if not prefix.lower().startswith(suffix.lower()):
        return None
    return prefix


def _make_record(
    title: str,
    href: str,
    board: dict[str, Any],
    *,
    source: str | None = None,
    location: str | None = None,
) -> DiscoveredJobRecord:
    company_name = lookup_company("successfactors", board_url=board.get("url"), explicit_company=board.get("company"))
    # Fallback: extract location from the URL slug when upstream paths
    # (LD-JSON / DOM / network JSON) didn't yield anything. Common on SF sites
    # that redirect to a branded careers host and render location only in URLs.
    location_source = "upstream" if location else None
    if not location:
        url_loc = _extract_location_from_url(href)
        if url_loc:
            location = url_loc
            location_source = "url_slug"
    listing_payload: dict[str, Any] = {}
    if source:
        listing_payload["source"] = source
    if location:
        listing_payload["location"] = location
        if location_source:
            listing_payload["location_source"] = location_source
    completeness = 0.5 if href else 0.25
    if location:
        completeness += 0.15
    return DiscoveredJobRecord(
        external_job_id=href or title,
        title_raw=_clean(title),
        location_raw=location,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload=listing_payload or None,
        completeness_score=round(min(completeness, 1.0), 4),
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


def _format_address(node: dict[str, Any]) -> str | None:
    """Extract a readable location string from a schema.org PostalAddress."""
    parts: list[str] = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        val = node.get(key)
        if isinstance(val, dict):
            val = val.get("name") or val.get("@value")
        if val:
            text = str(val).strip()
            if text and text not in parts:
                parts.append(text)
    return ", ".join(parts) if parts else None


def _extract_ld_json_location(node: dict[str, Any]) -> str | None:
    job_location = node.get("jobLocation")
    if not job_location:
        return None
    # jobLocation can be a single object or list of objects; normalise.
    candidates = job_location if isinstance(job_location, list) else [job_location]
    for loc in candidates:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address")
        if isinstance(address, dict):
            formatted = _format_address(address)
            if formatted:
                return formatted
        name = loc.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _extract_json_location(node: Any) -> str | None:
    """Pull a location-like string from an SF/SAP API payload.

    Walks common key shapes without assuming a fixed schema — SF Recruiting,
    SAP CareerSite, and third-party SF layers all differ.
    """
    if not isinstance(node, dict):
        return None
    # Direct string values on the node itself
    for key in ("jobLocation", "location", "locationDescription", "cityState", "locationCity"):
        val = node.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Nested list: locations[0].name / locations[0].city
    locations = node.get("locations")
    if isinstance(locations, list) and locations and isinstance(locations[0], dict):
        first = locations[0]
        for key in ("name", "city", "locationName", "displayName"):
            val = first.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    # Composite city + country
    city = node.get("city") or node.get("locationCity")
    country = node.get("country") or node.get("countryName") or node.get("locationCountry")
    parts = [p.strip() for p in (city, country) if isinstance(p, str) and p.strip()]
    if parts:
        return ", ".join(parts)
    return None


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


async def _collect_control_samples(page: Any, label: str, diagnostics: AdapterDiagnostics) -> None:
    try:
        inputs = await page.eval_on_selector_all(
            "input, select, textarea",
            """
            els => els.slice(0, 20).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type') || '',
                id: el.id || '',
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || '',
                value: el.value || ''
            }))
            """,
        )
        diagnostics.metadata[f"{label}_control_samples"] = inputs
        diagnostics.counters[f"{label}_control_count"] = len(inputs)
    except Exception:
        pass
    try:
        buttons = await page.eval_on_selector_all(
            "button, input[type='submit'], a[role='button']",
            """
            els => els.slice(0, 20).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type') || '',
                id: el.id || '',
                name: el.getAttribute('name') || '',
                text: (el.innerText || el.value || '').trim().slice(0, 120)
            }))
            """,
        )
        diagnostics.metadata[f"{label}_button_samples"] = buttons
        diagnostics.counters[f"{label}_button_count"] = len(buttons)
    except Exception:
        pass


async def _scroll_page(page: Any, diagnostics: AdapterDiagnostics, *, rounds: int = 3, pause_ms: int = 1200) -> None:
    for _ in range(rounds):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(pause_ms)
            diagnostics.counters["scroll_rounds"] = diagnostics.counters.get("scroll_rounds", 0) + 1
        except Exception:
            diagnostics.counters["scroll_failures"] = diagnostics.counters.get("scroll_failures", 0) + 1
            break


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
    try:
        body_text = await page.locator("body").inner_text()
        sample = body_text[:600].replace("\n", " ").strip()
        diagnostics.metadata[f"{label}_body_text_sample"] = sample
        lowered = sample.lower()
        diagnostics.metadata[f"{label}_body_text_flags"] = {
            "contains_sign_in": "sign in" in lowered,
            "contains_no_results": "no results" in lowered,
            "contains_search_results": "search results" in lowered,
            "contains_jobs_found": "jobs found" in lowered,
            "contains_search_for_openings": "search for openings" in lowered,
        }
    except Exception:
        pass
    await _collect_control_samples(page, label, diagnostics)


async def _extract_ld_json_records(page: Any, board_url: str, board: dict[str, Any]) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    try:
        scripts = await page.query_selector_all("script[type='application/ld+json']")
    except Exception:
        return records
    for script in scripts:
        try:
            raw = await script.inner_text()
            payload = json.loads(raw)
        except Exception:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                node_type = str(node.get("@type") or "")
                if node_type.lower() == "jobposting":
                    title = _clean(node.get("title"))
                    href = _absolute_url(node.get("url"), board_url)
                    if _looks_like_job_title(title) and _looks_like_job_url(href):
                        location = _extract_ld_json_location(node)
                        records.append(
                            _make_record(
                                title or href or "",
                                href or board_url,
                                board,
                                source="ld_json",
                                location=location,
                            )
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    deduped: list[DiscoveredJobRecord] = []
    seen: set[str] = set()
    for record in records:
        url = record.discovered_url or record.external_job_id
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(record)
    return deduped


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
                if not _looks_like_job_title(title) or not _looks_like_job_url(href):
                    continue
                location = None
                for loc_selector in _LOCATION_HINT_SELECTORS:
                    try:
                        loc_el = await el.query_selector(loc_selector)
                    except Exception:
                        continue
                    if not loc_el:
                        continue
                    try:
                        loc_text = (await loc_el.inner_text()).strip()
                    except Exception:
                        continue
                    if loc_text and len(loc_text) <= 120 and loc_text.lower().rstrip(":") not in {"location", "city", "office"}:
                        location = loc_text
                        break
                if location:
                    diagnostics.counters["dom_locations_found"] = diagnostics.counters.get("dom_locations_found", 0) + 1
                records.append(_make_record(title, href, board, source=source, location=location))
            except Exception:
                diagnostics.counters["element_parse_failures"] = diagnostics.counters.get("element_parse_failures", 0) + 1
        if records:
            return records
    return records


async def _extract_records(page: Any, board_url: str, board: dict[str, Any], diagnostics: AdapterDiagnostics) -> list[DiscoveredJobRecord]:
    parsed = await _extract_ld_json_records(page, board_url, board)
    if parsed:
        diagnostics.counters["ld_json_records_found"] = diagnostics.counters.get("ld_json_records_found", 0) + len(parsed)
        return parsed
    parsed = await _extract_records_from_scope(page, board_url, board, diagnostics, source="page")
    if parsed:
        return parsed
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
                return frame_records
        except Exception as exc:
            diagnostics.warnings.append(f"SuccessFactors frame inspection failed for {frame.url}: {exc}")
    return []


async def _run_external_site_search(page: Any, term: str, diagnostics: AdapterDiagnostics) -> None:
    current_url = page.url.lower()
    if "jobs.royallondon.com" not in current_url:
        return
    try:
        q_input = await page.query_selector("input[name='q'], input[placeholder*='keyword' i]")
        if not q_input:
            diagnostics.counters["external_site_search_box_misses"] = diagnostics.counters.get("external_site_search_box_misses", 0) + 1
            return
        await q_input.click()
        try:
            await q_input.press("Meta+A")
        except Exception:
            pass
        await q_input.fill(term)
        diagnostics.counters["external_site_terms_applied"] = diagnostics.counters.get("external_site_terms_applied", 0) + 1
    except Exception:
        diagnostics.counters["external_site_search_box_failures"] = diagnostics.counters.get("external_site_search_box_failures", 0) + 1
        return

    try:
        buttons = await page.query_selector_all("button, input[type='submit']")
        diagnostics.counters["external_site_button_count"] = len(buttons)
        for button in buttons[:10]:
            try:
                text = (_clean(await button.inner_text()) or _clean(await button.get_attribute("value")) or "").lower()
                if "search jobs" not in text and "search" not in text:
                    continue
                await button.click()
                await page.wait_for_timeout(2500)
                diagnostics.counters["external_site_search_button_clicks"] = diagnostics.counters.get("external_site_search_button_clicks", 0) + 1
                break
            except Exception:
                diagnostics.counters["external_site_search_button_failures"] = diagnostics.counters.get("external_site_search_button_failures", 0) + 1
        else:
            await q_input.press("Enter")
            await page.wait_for_timeout(2500)
            diagnostics.counters["external_site_enter_submits"] = diagnostics.counters.get("external_site_enter_submits", 0) + 1
    except Exception:
        diagnostics.counters["external_site_submit_failures"] = diagnostics.counters.get("external_site_submit_failures", 0) + 1


def _records_from_json_payload(payload: Any, board_url: str, board: dict[str, Any], source: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            title = _clean(
                node.get("jobTitle")
                or node.get("job_title")
                or node.get("title")
                or node.get("displayTitle")
                or node.get("postingTitle")
                or node.get("externalTitle")
            )
            href = _absolute_url(
                node.get("jobUrl")
                or node.get("url")
                or node.get("applyUrl")
                or node.get("jobReqIdUrl")
                or node.get("job_req_id")
                or node.get("jobReqId"),
                board_url,
            )
            if _looks_like_job_title(title) and _looks_like_job_url(href):
                location = _extract_json_location(node)
                records.append(
                    _make_record(
                        title or href or "",
                        href or board_url,
                        board,
                        source=source,
                        location=location,
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    deduped: list[DiscoveredJobRecord] = []
    seen_urls: set[str] = set()
    for record in records:
        url = record.discovered_url or record.external_job_id
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(record)
    return deduped


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

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("SuccessFactorsAdapter requires job_board_url")
        # Always start with an empty-string pass to scrape all visible jobs, then optionally search by term
        extra_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        search_terms = [""] + extra_terms  # "" = no keyword filter
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
                            captured_payloads: list[Any] = []
                            captured_urls: list[str] = []

                            async def on_response(response: Any) -> None:
                                try:
                                    if response.status != 200:
                                        return
                                    url = response.url
                                    lowered_url = url.lower()
                                    if not any(hint in lowered_url for hint in NETWORK_HINTS):
                                        return
                                    diagnostics.counters["network_candidate_responses"] = diagnostics.counters.get("network_candidate_responses", 0) + 1
                                    if len(captured_urls) < 20:
                                        captured_urls.append(url)
                                    content_type = (response.headers or {}).get("content-type", "")
                                    if "json" not in content_type.lower():
                                        return
                                    payload = await response.json()
                                    captured_payloads.append(payload)
                                except Exception:
                                    diagnostics.counters["network_capture_failures"] = diagnostics.counters.get("network_capture_failures", 0) + 1

                            page.on("response", on_response)
                            search_url = f"{board_url}&navBarLevel=JOB_SEARCH" if "?" in board_url else f"{board_url}?navBarLevel=JOB_SEARCH"
                            try:
                                await page.goto(search_url, wait_until="networkidle", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                                await page.wait_for_timeout(2000)
                            except Exception:
                                await page.goto(search_url, wait_until="domcontentloaded", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                                await page.wait_for_timeout(4000)

                            await _diagnose_page(page, diagnostics, board_url, f"term_{term}")

                            no_keyword_records = await _extract_records(page, page.url or board_url, board, diagnostics)
                            diagnostics.counters[f"term_{term}_no_keyword_records"] = len(no_keyword_records)
                            if no_keyword_records:
                                records_before_nk = len(all_records)
                                for record in no_keyword_records:
                                    href = record.discovered_url
                                    if href and href not in seen_urls:
                                        seen_urls.add(href)
                                        all_records.append(record)
                                if on_page_scraped and len(all_records) > records_before_nk:
                                    try:
                                        on_page_scraped(search_terms.index(term) + 1, all_records[records_before_nk:], all_records)
                                    except Exception:
                                        pass
                                page.remove_listener("response", on_response)
                                continue

                            try:
                                search_input = await page.query_selector(SEARCH_INPUT_SELECTOR)
                                if search_input:
                                    await search_input.click()
                                    try:
                                        await search_input.press("Meta+A")
                                    except Exception:
                                        pass
                                    await search_input.fill(term)
                                    try:
                                        form_handle = await search_input.evaluate_handle("el => el.form")
                                        if form_handle:
                                            await form_handle.evaluate("form => form && form.requestSubmit ? form.requestSubmit() : null")
                                            await page.wait_for_timeout(2000)
                                            diagnostics.counters["form_submit_attempts"] = diagnostics.counters.get("form_submit_attempts", 0) + 1
                                    except Exception:
                                        diagnostics.counters["form_submit_failures"] = diagnostics.counters.get("form_submit_failures", 0) + 1
                                    await search_input.press("Enter")
                                    await page.wait_for_timeout(2500)
                                    diagnostics.counters["search_terms_applied"] = diagnostics.counters.get("search_terms_applied", 0) + 1
                                else:
                                    diagnostics.counters["search_box_misses"] = diagnostics.counters.get("search_box_misses", 0) + 1
                            except Exception:
                                diagnostics.counters["search_box_misses"] = diagnostics.counters.get("search_box_misses", 0) + 1

                            try:
                                buttons = await page.query_selector_all(SEARCH_BUTTON_SELECTOR)
                                diagnostics.counters[f"term_{term}_candidate_buttons"] = len(buttons)
                                for button in buttons[:6]:
                                    try:
                                        text = (_clean(await button.inner_text()) or _clean(await button.get_attribute("value")) or "").lower()
                                        if text and not any(tok in text for tok in ("search", "find", "apply", "go", "submit")):
                                            continue
                                        await button.click()
                                        await page.wait_for_timeout(2000)
                                        diagnostics.counters["search_button_clicks"] = diagnostics.counters.get("search_button_clicks", 0) + 1
                                        break
                                    except Exception:
                                        diagnostics.counters["search_button_failures"] = diagnostics.counters.get("search_button_failures", 0) + 1
                            except Exception:
                                diagnostics.counters["search_button_misses"] = diagnostics.counters.get("search_button_misses", 0) + 1

                            await _scroll_page(page, diagnostics)
                            await _run_external_site_search(page, term, diagnostics)
                            await _scroll_page(page, diagnostics, rounds=2)
                            await _diagnose_page(page, diagnostics, page.url or board_url, f"term_{term}_post_search")
                            diagnostics.metadata[f"term_{term}_network_urls"] = captured_urls[:12]
                            diagnostics.counters[f"term_{term}_network_payloads"] = len(captured_payloads)

                            parsed: list[DiscoveredJobRecord] = []
                            for index, payload in enumerate(captured_payloads):
                                records = _records_from_json_payload(payload, page.url or board_url, board, source=f"network_{index}")
                                if records:
                                    parsed.extend(records)
                                    if len(parsed) >= 50:
                                        break

                            if parsed:
                                diagnostics.counters["network_records_found"] = diagnostics.counters.get("network_records_found", 0) + len(parsed)
                            else:
                                parsed = await _extract_records(page, page.url or board_url, board, diagnostics)

                            records_before = len(all_records)
                            for record in parsed:
                                href = record.discovered_url
                                if record.title_raw:
                                    diagnostics.counters["titles_seen"] = diagnostics.counters.get("titles_seen", 0) + 1
                                if href and href not in seen_urls:
                                    seen_urls.add(href)
                                    all_records.append(record)
                            if on_page_scraped and len(all_records) > records_before:
                                try:
                                    on_page_scraped(search_terms.index(term) + 1, all_records[records_before:], all_records)
                                except Exception:
                                    pass

                            page.remove_listener("response", on_response)
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
