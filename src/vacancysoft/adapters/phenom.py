from __future__ import annotations

import json
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
from vacancysoft.source_registry.legacy_board_mappings import lookup_company


PAGE_TIMEOUT_MS = 60_000
NETWORK_HINTS = ("jobs", "search-results", "job", "phenom")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _make_record(job: dict[str, Any], board: dict[str, Any], source: str) -> DiscoveredJobRecord | None:
    title = _clean(job.get("title") or job.get("jobTitle") or job.get("name") or job.get("displayTitle"))
    url = _clean(job.get("url") or job.get("jobUrl") or job.get("applyUrl") or job.get("externalPath"))
    if url and not url.startswith("http"):
        url = urljoin(str(board.get("url") or ""), url)
    if not title and not url:
        return None
    location = _clean(job.get("location") or job.get("city") or ((job.get("locations") or [None])[0] if isinstance(job.get("locations"), list) else None))
    posted_at = _clean(job.get("postedDate") or job.get("postedAt") or job.get("updatedAt"))
    company_name = lookup_company("phenom", board_url=board.get("url"), explicit_company=board.get("company"))
    completeness_fields = [title, location, url, posted_at]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
    return DiscoveredJobRecord(
        external_job_id=_clean(job.get("id")) or url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=None,
        discovered_url=url,
        apply_url=url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.8,
        provenance={
            "adapter": "phenom",
            "method": ExtractionMethod.BROWSER.value,
            "company": company_name or "",
            "platform": "Phenom",
            "board_url": str(board.get("url") or ""),
            "source": source,
        },
    )


def _walk(node: Any, board: dict[str, Any], source: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    if isinstance(node, dict):
        if any(k in node for k in ("jobTitle", "title", "applyUrl", "jobUrl", "externalPath")):
            record = _make_record(node, board, source)
            if record:
                records.append(record)
        for value in node.values():
            records.extend(_walk(value, board, source))
    elif isinstance(node, list):
        for item in node:
            records.extend(_walk(item, board, source))
    return records


class PhenomAdapter(SourceAdapter):
    adapter_name = "phenom"
    capabilities = AdapterCapabilities(supports_discovery=True, supports_detail_fetch=False, supports_healthcheck=False, supports_pagination=False, supports_incremental_sync=False, supports_api=False, supports_html=False, supports_browser=True, supports_site_rescue=False)

    # ── DISABLED 2026-04-22 ──────────────────────────────────────────
    # The audit (see scripts/audit_adapter_locations.py output in
    # artifacts/phenom-failing-2026-04-22.xlsx) showed 100% of the 68
    # "jobs" this adapter has ever produced are not jobs at all —
    # they're UI chrome (link/button dicts with
    # `{link, text, title, target, ariaLabel}`) and their `title_raw`
    # is the literal string "{'type': 'text', 'value': ''}".
    #
    # Root cause: `_walk()` above recurses into any dict containing a
    # "title" / "jobTitle" / "applyUrl" / "jobUrl" / "externalPath"
    # key (see line ~73), which matches Phenom's UI metadata structs.
    # The real job listings either live in a different XHR the
    # `NETWORK_HINTS` filter doesn't match, or require a DOM scrape
    # pass after network-idle that the current adapter skips.
    #
    # Fix needs a structural rewrite + fixture-based tests against
    # real Phenom career sites (Sei Investments, Cboe Clear Europe).
    # Until that lands the adapter is unregistered via this flag —
    # the registry walker in `adapters/__init__.py` skips it, and the
    # worker's ADAPTER_REGISTRY.get() lookup already handles the
    # missing-adapter case gracefully (logs a warning, skips the
    # source). Existing phenom sources in the DB stay active but are
    # never dispatched.
    disabled = True

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("PhenomAdapter requires job_board_url")
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url})
        if cursor is not None:
            diagnostics.warnings.append("PhenomAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("PhenomAdapter does not enforce incremental sync at source. since was ignored.")
        board = {"url": board_url, "company": source_config.get("company")}
        records: list[DiscoveredJobRecord] = []
        seen: set[str] = set()
        started = time.perf_counter()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                captured: list[Any] = []

                async def on_response(response: Any) -> None:
                    if response.status != 200:
                        return
                    lowered = response.url.lower()
                    if not any(hint in lowered for hint in NETWORK_HINTS):
                        return
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type.lower():
                        return
                    try:
                        captured.append(await response.json())
                    except Exception:
                        diagnostics.counters["json_intercept_failures"] = diagnostics.counters.get("json_intercept_failures", 0) + 1

                page.on("response", on_response)
                try:
                    await page.goto(board_url, wait_until="networkidle", timeout=int(source_config.get("page_timeout_ms", PAGE_TIMEOUT_MS)))
                    try:
                        next_data = await page.query_selector("script#__NEXT_DATA__")
                        if next_data:
                            captured.append(json.loads(await next_data.inner_text()))
                    except Exception:
                        diagnostics.counters["next_data_failures"] = diagnostics.counters.get("next_data_failures", 0) + 1
                    for payload in captured:
                        for record in _walk(payload, board, "network_or_next"):
                            key = record.discovered_url or record.external_job_id or ""
                            if key and key not in seen:
                                seen.add(key)
                                records.append(record)
                finally:
                    page.remove_listener("response", on_response)
                    await page.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Phenom page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Phenom browser failure: {exc}")
            raise
        diagnostics.counters["jobs_seen"] = len(records)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
