from __future__ import annotations

import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin

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
DOMAINS = [
    ("https://www.efinancialcareers.com", "eFinancialCareers"),
    ("https://www.efinancialcareers.co.uk", "eFinancialCareers UK"),
]
PAGE_TIMEOUT_MS = 45_000
SCROLL_PAUSE_MS = 1500
_JOB_RESPONSE_HINTS = re.compile(r"(jobTitle|job_title|displayTitle|totalJobs|totalCount|jobPostings|searchResults)", re.IGNORECASE)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_jobs_from_json(data: Any, base_domain: str, platform: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    if isinstance(data, dict):
        for key in ("data", "jobs", "results", "jobPostings", "searchResults", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        if isinstance(data, dict):
            return records
    if not isinstance(data, list):
        return records
    for job in data:
        if not isinstance(job, dict):
            continue
        title = _clean(job.get("jobTitle") or job.get("job_title") or job.get("title") or job.get("displayTitle"))
        if not title:
            continue
        company = job.get("companyName") or job.get("company") or job.get("advertiserName") or ((job.get("employer") or {}).get("name") if isinstance(job.get("employer"), dict) else None)
        location_obj = job.get("location") or job.get("jobLocation") or {}
        if isinstance(location_obj, dict):
            city = _clean(location_obj.get("city") or location_obj.get("name"))
            country = _clean(location_obj.get("country"))
            location = ", ".join(part for part in [city, country] if part) or None
        else:
            location = _clean(location_obj)
        slug = _clean(job.get("slug") or job.get("jobId") or job.get("id"))
        job_url = _clean(job.get("jobUrl") or job.get("url") or job.get("applyUrl"))
        if not job_url and slug:
            job_url = f"{base_domain}/jobs/{slug}"
        elif job_url and not job_url.startswith("http"):
            job_url = urljoin(base_domain, job_url)
        salary = job.get("salary") or job.get("salaryRange")
        if isinstance(salary, dict):
            low = salary.get("min") or salary.get("from")
            high = salary.get("max") or salary.get("to")
            currency = _clean(salary.get("currency")) or "£"
            if low and high:
                salary = f"{currency}{int(low):,} - {currency}{int(high):,}"
            elif low:
                salary = f"{currency}{int(low):,}+"
            else:
                salary = None
        salary = _clean(salary)
        contract = _clean(job.get("contractType") or job.get("employmentType"))
        summary_raw = " | ".join(part for part in [salary, contract] if part) or None
        posted = _clean(job.get("datePosted") or job.get("postedDate") or job.get("publishedDate") or job.get("createdAt"))
        completeness_fields = [title, location, job_url, posted]
        completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
        records.append(DiscoveredJobRecord(
            external_job_id=slug or job_url or title,
            title_raw=title,
            location_raw=location,
            posted_at_raw=posted,
            summary_raw=summary_raw,
            discovered_url=job_url,
            apply_url=job_url,
            listing_payload=job,
            completeness_score=round(completeness_score, 4),
            extraction_confidence=0.86,
            provenance={
                "adapter": "efinancialcareers",
                "method": ExtractionMethod.BROWSER.value,
                "company": _clean(company) or "",
                "platform": platform,
                "board_url": f"{base_domain}/search",
                "salary": salary,
                "contract_type": contract,
            },
        ))
    return records


class _EfcDomParser(HTMLParser):
    def __init__(self, base_domain: str, platform: str):
        super().__init__()
        self.base_domain = base_domain
        self.platform = platform
        self.records: list[DiscoveredJobRecord] = []
        self._in_title = False
        self._in_company = False
        self._in_location = False
        self._title = ""
        self._company = ""
        self._location = ""
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}
        cls = attrs_d.get("class", "")
        if tag == "a" and re.search(r"job.?title|jobTitle|position", cls, re.I):
            self._in_title = True
            href = attrs_d.get("href", "")
            self._href = href if href.startswith("http") else f"{self.base_domain}{href}"
        if tag in {"span", "div"} and re.search(r"company|employer|advertiser", cls, re.I):
            self._in_company = True
        if tag in {"span", "div"} and re.search(r"location|city", cls, re.I):
            self._in_location = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title += data
        if self._in_company:
            self._company += data
        if self._in_location:
            self._location += data

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            title = _clean(self._title)
            if title:
                self.records.append(DiscoveredJobRecord(
                    external_job_id=self._href or title,
                    title_raw=title,
                    location_raw=_clean(self._location),
                    posted_at_raw=None,
                    summary_raw=None,
                    discovered_url=self._href or self.base_domain,
                    apply_url=self._href or self.base_domain,
                    listing_payload=None,
                    completeness_score=0.5,
                    extraction_confidence=0.66,
                    provenance={
                        "adapter": "efinancialcareers",
                        "method": ExtractionMethod.BROWSER.value,
                        "company": _clean(self._company) or "",
                        "platform": self.platform,
                        "board_url": f"{self.base_domain}/search",
                        "fallback": "dom",
                    },
                ))
            self._in_title = False
            self._title = ""
            self._company = ""
            self._location = ""
            self._href = ""
        if self._in_company and tag in {"span", "div"}:
            self._in_company = False
        if self._in_location and tag in {"span", "div"}:
            self._in_location = False


def _parse_dom(html: str, base_domain: str, platform: str) -> list[DiscoveredJobRecord]:
    parser = _EfcDomParser(base_domain, platform)
    parser.feed(html)
    return parser.records


class EFinancialCareersAdapter(SourceAdapter):
    adapter_name = "efinancialcareers"
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
        terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        domains = source_config.get("domains") or DOMAINS
        diagnostics = AdapterDiagnostics(metadata={"search_terms": terms, "domain_count": len(domains)})
        if cursor is not None:
            diagnostics.warnings.append("EFinancialCareersAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("EFinancialCareersAdapter does not enforce incremental sync at source. since was ignored.")
        started = time.perf_counter()
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    for base_domain, platform in domains:
                        for term in terms:
                            intercepted_records: list[DiscoveredJobRecord] = []
                            async def on_response(response: Any) -> None:
                                if response.status != 200:
                                    return
                                content_type = response.headers.get("content-type", "")
                                if "json" not in content_type:
                                    return
                                if not re.search(r"(search|job|posting)", response.url, re.I):
                                    return
                                try:
                                    text = await response.text()
                                    if not _JOB_RESPONSE_HINTS.search(text):
                                        return
                                    intercepted_records.extend(_extract_jobs_from_json(json.loads(text), base_domain, platform))
                                except Exception:
                                    diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1
                            import json
                            page.on("response", on_response)
                            search_url = f"{base_domain}/search?q={quote_plus(term)}"
                            await page.goto(search_url, timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)), wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                            for _ in range(4):
                                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                await page.wait_for_timeout(int(source_config.get("scroll_pause_ms", SCROLL_PAUSE_MS)))
                            page.remove_listener("response", on_response)
                            parsed = intercepted_records or _parse_dom(await page.content(), base_domain, platform)
                            for record in parsed:
                                url = record.discovered_url or record.external_job_id
                                if not url or url in seen_urls:
                                    continue
                                seen_urls.add(url)
                                all_records.append(record)
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"eFinancialCareers page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"eFinancialCareers browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
