from __future__ import annotations

import json
import re
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
    PageCallback,
    SourceAdapter,
)
from vacancysoft.browser import browser_session
from vacancysoft.source_registry.legacy_board_mappings import lookup_company

PAGE_TIMEOUT_MS = 45_000

# CSS selectors for HiBob career page job listings
JOB_LINK_SELECTORS = [
    "a[href*='/job/']",
    "a[href*='/position/']",
    "a[class*='job']",
    "a[class*='Job']",
    "a[class*='position']",
    "[class*='job-card'] a",
    "[class*='job-list'] a",
    "[class*='opening'] a",
    "[class*='career'] a[href]",
]

_REJECT_TITLES = {
    "search", "apply", "learn more", "skip to main content",
    "welcome", "log in", "sign in", "cookie", "privacy",
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_job_url(href: str, base_host: str) -> bool:
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return False
    lower = href.lower()
    job_tokens = ("/job/", "/position/", "/jobs/", "/opening/", "/career/", "/vacancy/")
    return any(tok in lower for tok in job_tokens)


def _make_record(
    title: str | None,
    href: str,
    company_name: str,
    board_url: str,
    source_label: str,
) -> DiscoveredJobRecord:
    completeness = 0.5 if href else 0.25
    if title:
        completeness += 0.25
    return DiscoveredJobRecord(
        external_job_id=href or title or "unknown",
        title_raw=title,
        location_raw=None,
        posted_at_raw=None,
        summary_raw=None,
        discovered_url=href,
        apply_url=href,
        listing_payload={"href": href, "title": title, "source": source_label},
        completeness_score=round(completeness, 4),
        extraction_confidence=0.72,
        provenance={
            "adapter": "hibob",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name,
            "platform": "HiBob",
            "board_url": board_url,
            "source": source_label,
        },
    )


def _record_from_api_json(
    job: dict[str, Any],
    company_name: str,
    board_url: str,
) -> DiscoveredJobRecord | None:
    title = _clean(job.get("title") or job.get("name") or job.get("jobTitle"))
    if not title or len(title) < 4:
        return None

    job_id = _clean(job.get("id") or job.get("jobAdId"))
    location = _clean(job.get("location") or job.get("site") or job.get("country"))
    posted_at = _clean(job.get("createdAt") or job.get("publishedAt") or job.get("postedDate"))
    apply_url = _clean(job.get("applyUrl") or job.get("url") or job.get("jobUrl"))
    department = _clean(job.get("department"))
    employment_type = _clean(job.get("employmentType"))

    summary_parts = [p for p in [department, employment_type] if p]
    summary_raw = " | ".join(summary_parts) if summary_parts else None

    completeness_fields = [title, location, apply_url, posted_at]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=job_id or apply_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary_raw,
        discovered_url=apply_url,
        apply_url=apply_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.90,
        provenance={
            "adapter": "hibob",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name,
            "platform": "HiBob",
            "board_url": board_url,
            "source": "network_json",
        },
    )


async def _http_fast_path(
    board_url: str, company_name: str, diagnostics: AdapterDiagnostics
) -> list[DiscoveredJobRecord] | None:
    """Try the HiBob /api/job-ad REST endpoint directly — no browser needed."""
    import httpx

    parsed = urlparse(board_url)
    # Build API URL: https://{subdomain}.careers.hibob.com/api/job-ad
    origin = f"{parsed.scheme}://{parsed.netloc}"
    api_url = f"{origin}/api/job-ad"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                api_url,
                headers={
                    "Accept": "application/json",
                    "Referer": board_url,
                    "Origin": origin,
                },
            )
            if resp.status_code != 200:
                diagnostics.errors.append(f"HTTP fast path: {resp.status_code}")
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            job_ads = data.get("jobAdDetails", [])
            if not isinstance(job_ads, list):
                return None

            records: list[DiscoveredJobRecord] = []
            for job in job_ads:
                if not isinstance(job, dict):
                    continue
                title = _clean(job.get("title"))
                if not title or len(title) < 4:
                    continue
                job_id = _clean(job.get("id"))
                location = _clean(job.get("site") or job.get("country"))
                department = _clean(job.get("department"))
                employment_type = _clean(job.get("employmentType"))
                posted_at = _clean(job.get("publishedAt"))
                workspace_type = _clean(job.get("workspaceType"))

                # Build a job URL: /jobs/{id}
                apply_url = f"{origin}/jobs/{job_id}" if job_id else None

                summary_parts = [p for p in [department, employment_type, workspace_type] if p]
                summary_raw = " | ".join(summary_parts) if summary_parts else None

                completeness_fields = [title, location, apply_url, posted_at]
                completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

                records.append(DiscoveredJobRecord(
                    external_job_id=job_id or apply_url or title,
                    title_raw=title,
                    location_raw=location,
                    posted_at_raw=posted_at,
                    summary_raw=summary_raw,
                    discovered_url=apply_url,
                    apply_url=apply_url,
                    listing_payload=job,
                    completeness_score=round(completeness_score, 4),
                    extraction_confidence=0.95,
                    provenance={
                        "adapter": "hibob",
                        "method": ExtractionMethod.BROWSER.value,
                        "company": company_name,
                        "platform": "HiBob",
                        "board_url": board_url,
                        "source": "http_api_job_ad",
                    },
                ))
            diagnostics.counters["http_api_jobs"] = len(records)
            return records
    except Exception as exc:
        diagnostics.errors.append(f"HTTP fast path error: {exc}")
        return None


class HiBobAdapter(SourceAdapter):
    adapter_name = "hibob"
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
            raise ValueError("HiBob source_config requires job_board_url")

        company_name = lookup_company(
            "hibob",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        t0 = time.monotonic()

        # ── HTTP fast path: hit /api/job-ad directly ──
        records = await _http_fast_path(board_url, company_name, diagnostics)
        if records is not None and len(records) > 0:
            diagnostics.counters["total_jobs"] = len(records)
            diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)
            if on_page_scraped:
                await on_page_scraped(1, records, records)
            return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)

        # ── Browser fallback ──
        timeout_ms = int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS))
        records = []
        seen_urls: set[str] = set()
        network_payloads: list[dict] = []

        async with async_playwright() as pw:
            async with browser_session(pw) as (_browser, ctx):
                page = await ctx.new_page()

                # Intercept network responses for JSON job data
                async def _on_response(resp):
                    try:
                        url_lower = resp.url.lower()
                        if resp.status == 200 and any(
                            hint in url_lower
                            for hint in ("job", "position", "career", "hiring", "api")
                        ):
                            ct = resp.headers.get("content-type", "")
                            if "json" in ct:
                                body = await resp.json()
                                network_payloads.append(body)
                    except Exception:
                        pass

                page.on("response", _on_response)

                try:
                    await page.goto(board_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await page.wait_for_timeout(3000)
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    diagnostics.errors.append(f"Navigation failed: {exc}")

                # Try to extract jobs from intercepted network JSON
                for payload in network_payloads:
                    jobs_list = None
                    if isinstance(payload, list):
                        jobs_list = payload
                    elif isinstance(payload, dict):
                        for key in ("jobs", "data", "results", "items", "jobAds", "jobAdDetails", "positions"):
                            candidate = payload.get(key)
                            if isinstance(candidate, list) and candidate:
                                jobs_list = candidate
                                break
                    if jobs_list:
                        for item in jobs_list:
                            if not isinstance(item, dict):
                                continue
                            rec = _record_from_api_json(item, company_name, board_url)
                            if rec and rec.discovered_url and rec.discovered_url not in seen_urls:
                                seen_urls.add(rec.discovered_url)
                                records.append(rec)

                diagnostics.counters["network_payloads"] = len(network_payloads)
                diagnostics.counters["network_jobs"] = len(records)

                # Fallback: scrape DOM for job links
                if not records:
                    for selector in JOB_LINK_SELECTORS:
                        try:
                            elements = await page.query_selector_all(selector)
                            for el in elements:
                                href = await el.get_attribute("href")
                                title = (await el.inner_text()).strip() if el else None
                                if not href:
                                    continue
                                href = urljoin(board_url, href)
                                if href in seen_urls:
                                    continue
                                if title and title.lower() in _REJECT_TITLES:
                                    continue
                                if _is_job_url(href, ""):
                                    seen_urls.add(href)
                                    records.append(
                                        _make_record(title, href, company_name, board_url, f"dom:{selector}")
                                    )
                        except (PlaywrightError, PlaywrightTimeoutError):
                            continue

                diagnostics.counters["dom_jobs"] = len(records) - diagnostics.counters.get("network_jobs", 0)
                await page.close()

        diagnostics.counters["total_jobs"] = len(records)
        diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)

        if on_page_scraped:
            await on_page_scraped(1, records, records)

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
