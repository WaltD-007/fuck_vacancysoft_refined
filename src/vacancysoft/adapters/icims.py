from __future__ import annotations

import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]
PAGE_TIMEOUT_MS = 45_000
ICIMS_NETWORK_HINTS = ("search", "jobs", "api", "json", "positions")
DOM_LINK_SELECTORS = [
    "a[href*='/jobs/']",
    "a[href*='jobdetail']",
    "a[href*='job']",
    "a[class*='job']",
    "a[class*='Job']",
    "[class*='job-card'] a",
    "[class*='opening'] a",
    "[class*='search-result'] a",
]
_REJECT_TITLES = {
    "search",
    "apply",
    "learn more",
    "skip branding",
    "skip to main content",
    "welcome page",
    "log back in!",
    "application faqs",
}
_REJECT_URL_TOKENS = (
    "#icims_content_iframe",
    "/jobs/intro",
    "/jobs/login",
    "icims.help",
    "platform_help",
)
_JOB_URL_RE = re.compile(r"/jobs/\d+/.+/job(?:\?|$)", re.IGNORECASE)


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
    return urljoin(board_url.rstrip("/") + "/", href)


def _build_search_url(board_url: str, term: str) -> str:
    params = urlencode({"searchKeyword": term, "in": 1, "ip": -1, "pr": 0, "hd": 0, "in_iframe": 1})
    return f"{board_url.rstrip('/')}/search?{params}"


def _looks_like_job_title(title: str | None) -> bool:
    if not title:
        return False
    lowered = title.strip().lower()
    if lowered in _REJECT_TITLES:
        return False
    if lowered.startswith("job posting title"):
        lowered = lowered.replace("job posting title", "", 1).strip()
    return len(lowered) >= 4


def _normalise_title(title: str | None) -> str | None:
    title = _clean(title)
    if not title:
        return None
    lowered = title.lower()
    for prefix in ("job posting title", "job title", "title"):
        if lowered.startswith(prefix):
            title = title[len(prefix):].strip()
            lowered = title.lower()
    return _clean(title)


def _clean_icims_location(raw: str | None) -> str | None:
    """Clean iCIMS location format like 'US-NY-New York' → 'New York, NY, US'."""
    if not raw:
        return None
    raw = raw.strip()
    # Pattern: CC-ST-City or CC-City
    parts = raw.split("-", 2)
    if len(parts) == 3:
        country, state, city = parts[0].strip(), parts[1].strip(), parts[2].strip()
        return f"{city}, {state}, {country}"
    elif len(parts) == 2:
        country, city = parts[0].strip(), parts[1].strip()
        return f"{city}, {country}"
    return raw


def _looks_like_job_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    if any(token in lowered for token in _REJECT_URL_TOKENS):
        return False
    return bool(_JOB_URL_RE.search(lowered)) or "jobdetail" in lowered or "jobid=" in lowered


def _record_from_icims_json(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _normalise_title(job.get("jobtitle") or job.get("title") or job.get("job_title") or job.get("displayTitle") or job.get("name"))
    if not _looks_like_job_title(title):
        return None
    job_id = _clean(job.get("id") or job.get("jobId") or job.get("req_id") or job.get("jobid"))
    job_url = _absolute_url(job.get("jobUrl") or job.get("url") or job.get("applyUrl"), board["url"])
    if not job_url:
        job_url = f"{board['url'].rstrip('/')}/jobs/{job_id}" if job_id else board["url"]
    if not _looks_like_job_url(job_url):
        return None
    location_obj = job.get("location") or job.get("jobLocation") or {}
    location = None
    if isinstance(location_obj, dict):
        location = _clean(location_obj.get("city") or location_obj.get("name") or location_obj.get("text"))
        state = _clean(location_obj.get("state") or location_obj.get("countrySubdivision"))
        if state:
            location = f"{location}, {state}" if location else state
    else:
        location = _clean(location_obj)
    company_name = lookup_company("icims", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
    posted_at = _clean(job.get("postedDate") or job.get("datePosted") or job.get("updatedDate"))
    completeness_fields = [title, location, job_url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=job_id or job_url,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=None,
        discovered_url=job_url,
        apply_url=job_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.9,
        provenance={
            "adapter": "icims",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "iCIMS",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
        },
    )


class _IcimsDomParser(HTMLParser):
    def __init__(self, board: dict[str, Any]):
        super().__init__()
        self.board = board
        self.records: list[DiscoveredJobRecord] = []
        self._in_title = False
        self._in_location = False
        self._in_card = False
        self._current_title = ""
        self._current_href = ""
        self._current_location = ""
        self._skip_sr_only = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = attrs_d.get("class", "")
        href = attrs_d.get("href", "")
        # Detect job card
        if tag == "li" and "jobcarditem" in cls.lower().replace(" ", "").replace("_", ""):
            self._in_card = True
            self._current_location = ""
        # Detect location container: "header left" div or explicit location/headerfield class
        if tag in {"span", "div", "li"} and (
            "location" in cls.lower()
            or "icims_jobheaderfield" in cls.lower()
            or ("header" in cls.lower() and "left" in cls.lower())
        ):
            self._in_location = True
        # Skip sr-only label text ("Job Locations", "Title")
        if tag == "span" and "sr-only" in cls.lower():
            self._skip_sr_only = True
        # Detect title link
        if tag == "a" and (
            "job-title" in cls.lower()
            or "icims_jobtitle" in cls.lower()
            or "icims_anchor" in cls.lower()
            or "icims_anchorlink" in attrs_d.get("id", "").lower()
            or "/jobs/" in href.lower()
            or "jobdetail" in href.lower()
        ):
            self._in_title = True
            self._in_location = False  # Stop capturing location once we hit the title
            self._current_href = _absolute_url(href, self.board["url"]) or self.board["url"]

    def handle_data(self, data: str) -> None:
        if self._skip_sr_only:
            return
        if self._in_title:
            self._current_title += data
        elif self._in_location:
            self._current_location += data

    def handle_endtag(self, tag: str) -> None:
        if self._skip_sr_only and tag == "span":
            self._skip_sr_only = False
        if self._in_title and tag == "a":
            title = _normalise_title(self._current_title)
            # Clean location: strip "US-NY-" prefixes → "New York"
            raw_loc = _clean(self._current_location)
            location = _clean_icims_location(raw_loc)
            company_name = lookup_company("icims", board_url=self.board.get("url"), slug=self.board.get("slug"), explicit_company=self.board.get("company"))
            if _looks_like_job_title(title) and _looks_like_job_url(self._current_href):
                self.records.append(
                    DiscoveredJobRecord(
                        external_job_id=self._current_href or title,
                        title_raw=title,
                        location_raw=location,
                        posted_at_raw=None,
                        summary_raw=None,
                        discovered_url=self._current_href or self.board["url"],
                        apply_url=self._current_href or self.board["url"],
                        listing_payload=None,
                        completeness_score=0.6667,
                        extraction_confidence=0.72,
                        provenance={
                            "adapter": "icims",
                            "method": ExtractionMethod.BROWSER.value,
                            "company": company_name or "",
                            "platform": "iCIMS",
                            "board_url": str(self.board.get("url") or ""),
                            "board_slug": str(self.board.get("slug") or ""),
                            "fallback": "dom",
                        },
                    )
                )
            self._in_title = False
            self._current_title = ""
            self._current_href = ""
            self._current_location = ""
        if self._in_location and tag in {"div"}:
            self._in_location = False
        if tag == "li" and self._in_card:
            self._in_card = False
            self._current_location = ""


def _parse_icims_json_payload(payload: dict[str, Any] | list[Any], board: dict[str, Any], diagnostics: AdapterDiagnostics | None = None, *, source: str | None = None) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            if any(key in node for key in ("jobtitle", "title", "job_title", "displayTitle", "name")):
                record = _record_from_icims_json(node, board)
                if record:
                    records.append(record)
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(payload)
    deduped: list[DiscoveredJobRecord] = []
    seen: set[str] = set()
    for record in records:
        url = record.discovered_url or record.external_job_id
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(record)
    if diagnostics is not None and source and isinstance(payload, dict):
        diagnostics.metadata[f"{source}_payload_keys"] = sorted(list(payload.keys()))[:40]
    return deduped


def _parse_icims_dom(html: str, board: dict[str, Any]) -> list[DiscoveredJobRecord]:
    parser = _IcimsDomParser(board)
    parser.feed(html)
    return parser.records


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


async def _diagnose_scope(scope: Any, diagnostics: AdapterDiagnostics, board_url: str, label: str) -> None:
    try:
        url = scope.url if hasattr(scope, "url") else None
        if url is not None:
            diagnostics.metadata[f"{label}_page_url"] = url
    except Exception:
        pass
    try:
        if hasattr(scope, "title"):
            diagnostics.metadata[f"{label}_page_title"] = await scope.title()
    except Exception:
        pass
    try:
        anchors = await scope.query_selector_all("a")
        diagnostics.counters[f"{label}_anchor_count"] = len(anchors)
        diagnostics.metadata[f"{label}_anchor_samples"] = await _collect_anchor_samples(scope, board_url)
    except Exception:
        pass
    try:
        locator = scope.locator("body") if hasattr(scope, "locator") else None
        if locator is not None:
            body_text = await locator.inner_text()
            diagnostics.metadata[f"{label}_body_text_sample"] = body_text[:700].replace("\n", " ").strip()
    except Exception:
        pass


async def _extract_records_from_scope(scope: Any, board: dict[str, Any], diagnostics: AdapterDiagnostics, *, label: str) -> list[DiscoveredJobRecord]:
    html = ""
    try:
        html = await scope.content() if hasattr(scope, "content") else ""
    except Exception:
        diagnostics.counters[f"{label}_content_failures"] = diagnostics.counters.get(f"{label}_content_failures", 0) + 1
    if html:
        parsed = _parse_icims_dom(html, board)
        if parsed:
            diagnostics.counters[f"{label}_html_parser_records"] = len(parsed)
            return parsed
    try:
        for selector in DOM_LINK_SELECTORS:
            links = await scope.query_selector_all(selector)
            if not links:
                continue
            diagnostics.metadata[f"{label}_dom_selector_used"] = selector
            diagnostics.counters[f"{label}_dom_elements_seen"] = len(links)
            parsed: list[DiscoveredJobRecord] = []
            for link in links:
                title = _normalise_title(await link.inner_text())
                href = _absolute_url(await link.get_attribute("href"), board["url"])
                if _looks_like_job_title(title) and _looks_like_job_url(href):
                    company_name = lookup_company("icims", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
                    parsed.append(
                        DiscoveredJobRecord(
                            external_job_id=href or title,
                            title_raw=title,
                            location_raw=None,
                            posted_at_raw=None,
                            summary_raw=None,
                            discovered_url=href or board["url"],
                            apply_url=href or board["url"],
                            listing_payload=None,
                            completeness_score=0.5,
                            extraction_confidence=0.7,
                            provenance={
                                "adapter": "icims",
                                "method": ExtractionMethod.BROWSER.value,
                                "company": company_name or "",
                                "platform": "iCIMS",
                                "board_url": str(board.get("url") or ""),
                                "board_slug": str(board.get("slug") or ""),
                                "fallback": label,
                            },
                        )
                    )
            if parsed:
                return parsed
    except Exception:
        diagnostics.counters[f"{label}_dom_query_failures"] = diagnostics.counters.get(f"{label}_dom_query_failures", 0) + 1
    return []


class IcimsAdapter(SourceAdapter):
    adapter_name = "icims"
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
            raise ValueError("IcimsAdapter requires job_board_url")
        slug = str(source_config.get("slug") or "").strip() or None
        board = {"url": board_url, "slug": slug, "company": source_config.get("company")}
        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "search_terms": search_terms})
        if cursor is not None:
            diagnostics.warnings.append("IcimsAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("IcimsAdapter does not enforce incremental sync at source. since was ignored.")

        started = time.perf_counter()
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()

        # ── HTTP fast path: try fetching in_iframe=1 HTML directly ──
        board = {"url": board_url, "slug": slug, "company": source_config.get("company")}
        try:
            http_url = _build_search_url(board_url, "")
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(http_url)
            if resp.status_code == 200:
                http_records = _parse_icims_dom(resp.text, board)
                for rec in http_records:
                    url_key = rec.discovered_url or rec.external_job_id
                    if url_key and url_key not in seen_urls:
                        seen_urls.add(url_key)
                        all_records.append(rec)
                diagnostics.counters["http_fast_path_records"] = len(all_records)
                if all_records:
                    diagnostics.metadata["method"] = "http_fast_path"
                    if on_page_scraped:
                        try:
                            on_page_scraped(1, all_records, all_records)
                        except Exception:
                            pass
                    diagnostics.counters["jobs_seen"] = len(all_records)
                    diagnostics.counters["unique_urls"] = len(seen_urls)
                    diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
                    return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
        except Exception:
            diagnostics.counters["http_fast_path_failed"] = 1

        # ── Browser fallback ──
        try:
            async with async_playwright() as playwright:
                async with browser_session(playwright) as (_browser, context):
                    page = await context.new_page()
                    try:
                        for term in search_terms:
                            intercepted: list[dict[str, Any] | list[Any]] = []
                            captured_urls: list[str] = []

                            async def handle_response(response: Any) -> None:
                                url = response.url
                                if response.status != 200:
                                    return
                                lowered = url.lower()
                                if not any(token in lowered for token in ICIMS_NETWORK_HINTS):
                                    return
                                diagnostics.counters["network_candidate_responses"] = diagnostics.counters.get("network_candidate_responses", 0) + 1
                                if len(captured_urls) < 20:
                                    captured_urls.append(url)
                                content_type = response.headers.get("content-type", "")
                                if "json" not in content_type.lower():
                                    return
                                try:
                                    intercepted.append(await response.json())
                                except Exception:
                                    diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1

                            page.on("response", handle_response)
                            search_url = _build_search_url(board_url, term)
                            try:
                                await page.goto(search_url, timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)), wait_until="networkidle")
                            except Exception:
                                await page.goto(search_url, timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)), wait_until="domcontentloaded")
                                await page.wait_for_timeout(4000)

                            await _diagnose_scope(page, diagnostics, board_url, f"term_{term}")
                            diagnostics.counters[f"term_{term}_frame_count"] = len(page.frames)
                            diagnostics.metadata[f"term_{term}_frame_urls"] = [frame.url for frame in page.frames[:8]]
                            diagnostics.metadata[f"term_{term}_network_urls"] = captured_urls[:12]
                            diagnostics.counters[f"term_{term}_network_payloads"] = len(intercepted)

                            parsed: list[DiscoveredJobRecord] = []
                            for idx, payload in enumerate(intercepted):
                                parsed.extend(_parse_icims_json_payload(payload, board, diagnostics, source=f"term_{term}_payload_{idx}"))

                            iframe = None
                            for frame in page.frames:
                                try:
                                    if "in_iframe=1" in (frame.url or ""):
                                        iframe = frame
                                        break
                                except Exception:
                                    continue

                            if iframe is not None:
                                await _diagnose_scope(iframe, diagnostics, board_url, f"term_{term}_iframe")
                                iframe_records = await _extract_records_from_scope(iframe, board, diagnostics, label=f"term_{term}_iframe")
                                if iframe_records:
                                    parsed.extend(iframe_records)

                            if not parsed:
                                page_records = await _extract_records_from_scope(page, board, diagnostics, label=f"term_{term}_page")
                                if page_records:
                                    parsed.extend(page_records)

                            records_before = len(all_records)
                            for record in parsed:
                                url = record.discovered_url or record.external_job_id
                                if not url or url in seen_urls:
                                    continue
                                seen_urls.add(url)
                                all_records.append(record)
                            if on_page_scraped and len(all_records) > records_before:
                                try:
                                    on_page_scraped(search_terms.index(term) + 1, all_records[records_before:], all_records)
                                except Exception:
                                    pass
                            page.remove_listener("response", handle_response)
                    finally:
                        await page.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Icims page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Icims browser failure: {exc}")
            raise

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
