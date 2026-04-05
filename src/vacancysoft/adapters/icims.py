from __future__ import annotations

import json
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]
PAGE_TIMEOUT_MS = 45_000


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_search_url(board_url: str, term: str) -> str:
    params = urlencode({"searchKeyword": term, "in": 1, "ip": -1, "pr": 0, "hd": 0})
    return f"{board_url.rstrip('/')}/jobs/search?{params}"


def _record_from_icims_json(job: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _clean(job.get("jobtitle") or job.get("title") or job.get("job_title") or job.get("displayTitle"))
    if not title:
        return None
    job_id = _clean(job.get("id") or job.get("jobId") or job.get("req_id"))
    job_url = _clean(job.get("jobUrl") or job.get("url"))
    if not job_url:
        job_url = f"{board['url'].rstrip('/')}/jobs/{job_id}" if job_id else board["url"]
    location_obj = job.get("location") or job.get("jobLocation") or {}
    location = None
    if isinstance(location_obj, dict):
        location = _clean(location_obj.get("city") or location_obj.get("name"))
        state = _clean(location_obj.get("state") or location_obj.get("countrySubdivision"))
        if state:
            location = f"{location}, {state}" if location else state
    else:
        location = _clean(location_obj)
    company_name = lookup_company("icims", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
    posted_at = _clean(job.get("postedDate") or job.get("datePosted"))
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
        if tag == "a" and ("job-title" in cls or "iCIMS_JobTitle" in cls or "iCIMS_AnchorLink" in attrs_d.get("id", "")):
            self._in_title = True
            href = attrs_d.get("href", "")
            self._current_href = href if href.startswith("http") else f"{self.board['url'].rstrip('/')}{href}"
        if tag in {"span", "div"} and ("location" in cls.lower() or "iCIMS_JobHeaderField" in cls):
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
            if title:
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
        if self._in_location and tag in {"span", "div"}:
            self._in_location = False


def _parse_icims_json_payload(payload: dict[str, Any] | list[Any], board: dict[str, Any]) -> list[DiscoveredJobRecord]:
    jobs = payload if isinstance(payload, list) else payload.get("jobs", payload.get("results", []))
    records: list[DiscoveredJobRecord] = []
    for job in jobs:
        if isinstance(job, dict):
            record = _record_from_icims_json(job, board)
            if record:
                records.append(record)
    return records


def _parse_icims_dom(html: str, board: dict[str, Any]) -> list[DiscoveredJobRecord]:
    parser = _IcimsDomParser(board)
    parser.feed(html)
    return parser.records


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
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    for term in search_terms:
                        intercepted: list[dict[str, Any] | list[Any]] = []

                        async def handle_response(response: Any) -> None:
                            if "search" not in response.url or response.status != 200:
                                return
                            content_type = response.headers.get("content-type", "")
                            if "json" not in content_type:
                                return
                            try:
                                intercepted.append(await response.json())
                            except Exception:
                                diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1

                        page.on("response", handle_response)
                        search_url = _build_search_url(board_url, term)
                        await page.goto(search_url, timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)), wait_until="networkidle")
                        parsed: list[DiscoveredJobRecord] = []
                        for payload in intercepted:
                            parsed.extend(_parse_icims_json_payload(payload, board))
                        if not parsed:
                            html = await page.content()
                            parsed = _parse_icims_dom(html, board)
                        for record in parsed:
                            url = record.discovered_url or record.external_job_id
                            if not url or url in seen_urls:
                                continue
                            seen_urls.add(url)
                            all_records.append(record)
                        page.remove_listener("response", handle_response)
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
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
