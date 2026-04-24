from __future__ import annotations

import json
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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats"]
PAGE_TIMEOUT_MS = 60_000
ORACLE_NETWORK_HINTS = (
    "recruitingcejobrequisitions",
    "searchjobrequisitions",
    "recruitingjobrequisitions",
    "job",
    "requisition",
    "candidateexperience",
)
DOM_SELECTORS = [
    "a[data-ph-at-id='job-title-link']",
    "a.job-title",
    "a[href*='/job/']",
    "a[href*='job']",
    "a[href*='requisition']",
    "[class*='job-title'] a",
    "[class*='jobTitle'] a",
    "[class*='opening'] a",
    "[class*='posting'] a",
    "[role='link']",
]
_REJECT_TITLE_TOKENS = {
    "skip to main content",
    "sign in",
    "search jobs",
    "candidate home",
    "privacy policy",
    # Date/posting-age filter chips from Oracle's left-hand filter panel
    "greater than 30 days",
    "more than 30 days",
    "less than 30 days",
    "less than 7 days",
    "less than 14 days",
    "less than 1 day",
    "on-site",
    "on site",
    "remote",
    "hybrid",
}
_REJECT_TITLE_PREFIXES = (
    "all jobs",
    "new jobs",
    "software engineering",
    "advisors",
    "associate bankers",
    "product management",
    "originations",
)
# Pattern reject list — matches UI chrome the token/prefix lists can't express.
_REJECT_TITLE_REGEXES = (
    # Date-range chips not covered by the token list: "More than 12 days"
    re.compile(r"^(greater|less|more)\s+than\s+\d+\s+day", re.IGNORECASE),
    # Oracle category-dropdown codes: "Executive.X", "Director.D", "Clerical.C",
    # "Manager.M", "Administrative.A", "Engineer/Consultant.E"
    re.compile(r"^[a-z][a-z /()-]{2,30}\.[a-z]$", re.IGNORECASE),
    # Oracle ERP business-unit / cost-centre codes:
    # "100201.Corporate Technology Admin", "293633-BUS DEVELOPMENT - 0802",
    # "0810253-FUND ACCTG IRELAND LUX FDS"
    re.compile(r"^\d{5,}[-.][A-Za-z]"),
)
# Country / region names that Oracle puts in its location facet dropdown and
# that leak in as 'titles'. Only fullmatch — "India" alone is chrome, but
# "India Head of Risk" is a real role.
_REJECT_TITLE_COUNTRY_ONLY = {
    "india", "united kingdom", "united states", "united states of america",
    "canada", "australia", "ireland", "germany", "france", "spain", "italy",
    "netherlands", "belgium", "switzerland", "luxembourg", "austria",
    "china", "japan", "hong kong", "singapore", "malaysia", "philippines",
    "thailand", "vietnam", "indonesia", "korea", "south korea", "taiwan",
    "uae", "united arab emirates", "saudi arabia", "qatar", "oman", "bahrain",
    "egypt", "israel", "jordan", "kuwait",
    "brazil", "mexico", "argentina", "chile", "colombia", "peru", "venezuela",
    "russia", "turkey", "poland", "czech republic", "romania", "hungary",
    "sweden", "norway", "denmark", "finland", "iceland",
    "south africa", "nigeria", "kenya", "morocco", "ghana",
    "pakistan", "bangladesh", "sri lanka", "nepal",
    "usa", "uk",
}
_REJECT_URL_TOKENS = ("privacy", "linkedin", "facebook", "twitter", "mailto:", "javascript:", "selectedpostingdatesfacet", "selectedcategoriesfacet")


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


def _looks_like_job_title(title: str | None) -> bool:
    if not title:
        return False
    lowered = title.strip().lower()
    if lowered in _REJECT_TITLE_TOKENS:
        return False
    if lowered in _REJECT_TITLE_COUNTRY_ONLY:
        return False
    if any(lowered.startswith(prefix) for prefix in _REJECT_TITLE_PREFIXES):
        return False
    if any(rx.match(title.strip()) for rx in _REJECT_TITLE_REGEXES):
        return False
    if re.fullmatch(r"[a-z &/-]+ \(\d+\)", lowered):
        return False
    return len(lowered) >= 4


def _looks_like_job_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    if any(token in lowered for token in _REJECT_URL_TOKENS):
        return False
    return any(token in lowered for token in ("/job/", "job?", "job/", "requisition", "posting", "jobdetail", "jobdetails"))


def _parse_requisition(req: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _clean(req.get("Title") or req.get("title") or req.get("RequisitionTitle") or req.get("PostingTitle") or req.get("Name"))
    if not _looks_like_job_title(title):
        return None
    location = req.get("PrimaryLocation") or req.get("primaryLocation") or req.get("WorkLocation") or req.get("LocationCity") or req.get("Location")
    if isinstance(location, dict):
        location = location.get("descriptor") or location.get("name") or location.get("value")
    location = _clean(location)
    req_id = _clean(req.get("Id") or req.get("RequisitionNumber") or req.get("id") or req.get("JobId") or req.get("jobId"))
    direct_url = _absolute_url(
        req.get("ExternalUrl")
        or req.get("jobUrl")
        or req.get("url")
        or req.get("jobURL")
        or req.get("externalURL"),
        str(board.get("url") or ""),
    )
    board_url = str(board.get("url") or "").rstrip("/")
    job_url = direct_url or (f"{board_url}/job/{req_id}" if req_id else None) or board_url
    posted = _clean(req.get("PostedDate") or req.get("postedDate") or req.get("LastUpdatedDate") or req.get("CreationDate"))
    company_name = lookup_company("oracle", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, job_url, posted]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=req_id or job_url,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted,
        summary_raw=None,
        discovered_url=job_url,
        apply_url=job_url,
        listing_payload=req,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.87,
        provenance={
            "adapter": "oracle",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "Oracle Cloud",
            "board_url": str(board.get("url") or ""),
            "source": "xhr",
        },
    )


def _extract_records_from_xhr(captured: list[dict[str, Any]], board: dict[str, Any], diagnostics: AdapterDiagnostics | None = None, *, term: str | None = None) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []

    for data in captured:
        items = data.get("items") or []
        if diagnostics is not None and term is not None:
            diagnostics.counters[f"term_{term}_item_count"] = len(items)
            if items:
                first = items[0]
                if isinstance(first, dict):
                    diagnostics.metadata[f"term_{term}_first_item_keys"] = sorted(list(first.keys()))[:50]
                    sample = {}
                    for key, value in first.items():
                        if isinstance(value, (str, int, float, bool)) or value is None:
                            sample[key] = value
                        elif isinstance(value, dict):
                            sample[key] = {k: v for k, v in list(value.items())[:8] if isinstance(v, (str, int, float, bool)) or v is None}
                        elif isinstance(value, list):
                            sample[key] = f"list[{len(value)}]"
                        else:
                            sample[key] = str(type(value).__name__)
                    diagnostics.metadata[f"term_{term}_first_item_sample"] = sample

        def walk(node: Any, depth: int = 0) -> None:
            if depth > 6:
                return
            if isinstance(node, dict):
                if any(key in node for key in ("Title", "title", "RequisitionTitle", "PostingTitle", "Name")):
                    record = _parse_requisition(node, board)
                    if record:
                        records.append(record)
                for value in node.values():
                    walk(value, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    walk(item, depth + 1)

        for item in items:
            walk(item)

    deduped: list[DiscoveredJobRecord] = []
    seen_urls: set[str] = set()
    for record in records:
        url = record.discovered_url or record.external_job_id
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(record)
    return deduped


def _make_dom_record(title: str, href: str, board: dict[str, Any], *, source: str) -> DiscoveredJobRecord:
    company_name = lookup_company("oracle", board_url=board.get("url"), explicit_company=board.get("company"))
    return DiscoveredJobRecord(
        external_job_id=href or title,
        title_raw=_clean(title),
        location_raw=None,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload={"source": source},
        completeness_score=0.5 if href else 0.25,
        extraction_confidence=0.72,
        provenance={
            "adapter": "oracle",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "Oracle Cloud",
            "board_url": str(board.get("url") or ""),
            "fallback": source,
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


async def _collect_control_samples(page: Any, label: str, diagnostics: AdapterDiagnostics) -> None:
    try:
        inputs = await page.eval_on_selector_all(
            "input, select, textarea, button",
            """
            els => els.slice(0, 25).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type') || '',
                id: el.id || '',
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || '',
                text: (el.innerText || el.value || '').trim().slice(0, 120)
            }))
            """,
        )
        diagnostics.metadata[f"{label}_control_samples"] = inputs
        diagnostics.counters[f"{label}_control_count"] = len(inputs)
    except Exception:
        pass


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
        sample = body_text[:700].replace("\n", " ").strip()
        diagnostics.metadata[f"{label}_body_text_sample"] = sample
        lowered = sample.lower()
        diagnostics.metadata[f"{label}_body_text_flags"] = {
            "contains_sign_in": "sign in" in lowered,
            "contains_search": "search" in lowered,
            "contains_jobs": "jobs" in lowered,
            "contains_no_results": "no results" in lowered,
            "contains_apply": "apply" in lowered,
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
            payload = json.loads(await script.inner_text())
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
                        records.append(_make_dom_record(title or href or "", href or board_url, board, source="ld_json"))
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


async def _extract_dom_records_from_scope(scope: Any, board: dict[str, Any], diagnostics: AdapterDiagnostics, *, source: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    board_url = str(board.get("url") or "")
    for selector in DOM_SELECTORS:
        try:
            links = await scope.query_selector_all(selector)
        except Exception:
            continue
        if not links:
            continue
        diagnostics.metadata[f"{source}_selector_used"] = selector
        diagnostics.counters[f"{source}_elements_seen"] = len(links)
        for link in links:
            try:
                title = _clean(await link.inner_text())
                href = _absolute_url(await link.get_attribute("href"), board_url)
                if not _looks_like_job_title(title) or not _looks_like_job_url(href):
                    continue
                records.append(_make_dom_record(title or href or "", href or board_url, board, source=source))
            except Exception:
                diagnostics.counters["dom_parse_failures"] = diagnostics.counters.get("dom_parse_failures", 0) + 1
        if records:
            return records
    return records


async def _dom_fallback(page: Any, board: dict[str, Any], diagnostics: AdapterDiagnostics) -> list[DiscoveredJobRecord]:
    records = await _extract_ld_json_records(page, str(board.get("url") or ""), board)
    if records:
        diagnostics.counters["ld_json_records_found"] = diagnostics.counters.get("ld_json_records_found", 0) + len(records)
        return records
    records = await _extract_dom_records_from_scope(page, board, diagnostics, source="page")
    if records:
        return records
    for index, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        try:
            frame_records = await _extract_dom_records_from_scope(frame, board, diagnostics, source=f"frame_{index}")
            if frame_records:
                diagnostics.metadata["hit_frame_url"] = frame.url
                return frame_records
        except Exception as exc:
            diagnostics.warnings.append(f"Oracle frame inspection failed for {frame.url}: {exc}")
    return []


class OracleCloudAdapter(SourceAdapter):
    adapter_name = "oracle"
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
            raise ValueError("OracleCloudAdapter requires job_board_url")
        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        board = {"url": board_url, "company": source_config.get("company")}
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "search_terms": search_terms})
        if cursor is not None:
            diagnostics.warnings.append("OracleCloudAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("OracleCloudAdapter does not enforce incremental sync at source. since was ignored.")
        started = time.perf_counter()
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        try:
            async with async_playwright() as playwright:
                async with browser_session(playwright) as (_browser, context):
                    page = await context.new_page()
                    try:
                        for term in search_terms:
                            captured: list[dict[str, Any]] = []
                            captured_urls: list[str] = []

                            async def on_response(response: Any) -> None:
                                url = response.url
                                if response.status != 200:
                                    return
                                lowered_url = url.lower()
                                if not any(key in lowered_url for key in ORACLE_NETWORK_HINTS):
                                    return
                                diagnostics.counters["network_candidate_responses"] = diagnostics.counters.get("network_candidate_responses", 0) + 1
                                if len(captured_urls) < 20:
                                    captured_urls.append(url)
                                if not any(key in lowered_url for key in ("recruitingcejobrequisitions", "searchjobrequisitions", "recruitingjobrequisitions")):
                                    return
                                try:
                                    data = await response.json()
                                    if isinstance(data, dict):
                                        captured.append(data)
                                except Exception:
                                    diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1

                            page.on("response", on_response)
                            search_url = f"{board_url}?keyword={term}&sortBy=POSTING_DATES_DESC"
                            try:
                                await page.goto(search_url, wait_until="networkidle", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                            except Exception:
                                await page.goto(search_url, wait_until="domcontentloaded", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                                await page.wait_for_timeout(int(source_config.get("search_settle_ms", 5000)))

                            await _diagnose_page(page, diagnostics, board_url, f"term_{term}")
                            diagnostics.metadata[f"term_{term}_network_urls"] = captured_urls[:12]
                            diagnostics.counters[f"term_{term}_network_payloads"] = len(captured)
                            if captured:
                                diagnostics.metadata[f"term_{term}_network_payload_keys"] = [sorted(list(payload.keys()))[:20] for payload in captured[:3]]

                            parsed = _extract_records_from_xhr(captured, board, diagnostics, term=term)
                            if parsed:
                                diagnostics.counters["xhr_records_found"] = diagnostics.counters.get("xhr_records_found", 0) + len(parsed)
                            else:
                                parsed = await _dom_fallback(page, board, diagnostics)
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
                            page.remove_listener("response", on_response)
                            await page.wait_for_timeout(int(source_config.get("between_searches_ms", 1500)))
                    finally:
                        await page.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Oracle Cloud page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Oracle Cloud browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
