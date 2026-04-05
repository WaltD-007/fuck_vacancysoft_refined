from __future__ import annotations

import json
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin

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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]
PAGE_TIMEOUT_MS = 45_000
SEARCH_INPUT_SELECTOR = (
    "input[name*='search' i], input[id*='search' i], input[placeholder*='search' i], "
    "input[placeholder*='keyword' i], input[type='search']"
)
ICIMS_NETWORK_HINTS = ("search", "jobs", "api", "json", "positions")
DOM_LINK_SELECTORS = [
    "a[href*='/jobs/']",
    "a[href*='jobdetail']",
    "a[href*='job']",
    "a[class*='job']",
    "a[class*='Job']",
]


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
    params = urlencode({"searchKeyword": term, "in": 1, "ip": -1, "pr": 0, "hd": 0})
    return f"{board_url.rstrip('/')}/search?{params}"


def _looks_like_job_title(title: str | None) -> bool:
    if not title:
        return False
    lowered = title.strip().lower()
    return len(lowered) >= 4 and lowered not in {"search", "apply", "learn more"}


def _looks_like_job_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(token in lowered for token in ("/jobs/", "jobdetail", "job?", "jobid"))


def _record_from_icims_json(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _clean(job.get("jobtitle") or job.get("title") or job.get("job_title") or job.get("displayTitle") or job.get("name"))
    if not _looks_like_job_title(title):
        return None
    job_id = _clean(job.get("id") or job.get("jobId") or job.get("req_id") or job.get("jobid"))
    job_url = _absolute_url(job.get("jobUrl") or job.get("url") or job.get("applyUrl"), board["url"])
    if not job_url:
        job_url = f"{board['url'].rstrip('/')}/jobs/{job_id}" if job_id else board["url"]
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
        self._current_title = ""
        self._current_href = ""
        self._current_location = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = attrs_d.get("class", "")
        href = attrs_d.get("href", "")
        if tag == "a" and (
            "job-title" in cls.lower()
            or "icims_jobtitle" in cls.lower()
            or "icims_anchorlink" in attrs_d.get("id", "").lower()
            or "/jobs/" in href.lower()
            or "jobdetail" in href.lower()
        ):
            self._in_title = True
            self._current_href = _absolute_url(href, self.board["url"]) or self.board["url"]
        if tag in {"span", "div", "li"} and ("location" in cls.lower() or "icims_jobheaderfield" in cls.lower()):
            self._in_location = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._current_title += data
        if self._in_location:
            self._current_location += data

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            title = _clean(self._current_title)
            location = _clean(self._current_location)
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
        if self._in_location and tag in {"span", "div", "li"}:
            self._in_location = False


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


async def _collect_anchor_samples(page: Any, board_url: str, limit: int = 8) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    try:
        anchors = await page.query_selector_all("a")
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
    try:
        body_text = await page.locator("body").inner_text()
        diagnostics.metadata[f"{label}_body_text_sample"] = body_text[:700].replace("\n", " ").strip()
    except Exception:
        pass


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

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None) -> DiscoveryPage:
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

                            await _diagnose_page(page, diagnostics, board_url, f"term_{term}")
                            diagnostics.metadata[f"term_{term}_network_urls"] = captured_urls[:12]
                            diagnostics.counters[f"term_{term}_network_payloads"] = len(intercepted)

                            parsed: list[DiscoveredJobRecord] = []
                            for idx, payload in enumerate(intercepted):
                                parsed.extend(_parse_icims_json_payload(payload, board, diagnostics, source=f"term_{term}_payload_{idx}"))
                            if not parsed:
                                html = await page.content()
                                parsed = _parse_icims_dom(html, board)
                                if not parsed:
                                    try:
                                        for selector in DOM_LINK_SELECTORS:
                                            links = await page.query_selector_all(selector)
                                            if not links:
                                                continue
                                            diagnostics.metadata[f"term_{term}_dom_selector_used"] = selector
                                            diagnostics.counters[f"term_{term}_dom_elements_seen"] = len(links)
                                            for link in links:
                                                title = _clean(await link.inner_text())
                                                href = _absolute_url(await link.get_attribute("href"), board_url)
                                                if _looks_like_job_title(title) and _looks_like_job_url(href):
                                                    company_name = lookup_company("icims", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
                                                    parsed.append(
                                                        DiscoveredJobRecord(
                                                            external_job_id=href or title,
                                                            title_raw=title,
                                                            location_raw=None,
                                                            posted_at_raw=None,
                                                            summary_raw=None,
                                                            discovered_url=href or board_url,
                                                            apply_url=href or board_url,
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
                                                                "fallback": "dom_query",
                                                            },
                                                        )
                                                    )
                                            if parsed:
                                                break
                                    except Exception:
                                        diagnostics.counters["dom_query_failures"] = diagnostics.counters.get("dom_query_failures", 0) + 1
                            for record in parsed:
                                url = record.discovered_url or record.external_job_id
                                if not url or url in seen_urls:
                                    continue
                                seen_urls.add(url)
                                all_records.append(record)
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
