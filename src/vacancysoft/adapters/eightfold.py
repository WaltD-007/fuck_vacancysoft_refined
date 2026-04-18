from __future__ import annotations

import asyncio
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

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats"]
DEFAULT_PAGE_TIMEOUT_MS = 60_000
DEFAULT_SEARCH_SETTLE_MS = 3_000
SEARCH_INPUT_SELECTORS = [
    "input[type='search']",
    "input[placeholder*='search' i]",
    "input[placeholder*='job' i]",
    "input[aria-label*='search' i]",
    "input[type='text']",
]
CARD_SELECTORS = [
    "[class*='position']",
    "[class*='job-card']",
    "[class*='jobcard']",
    "article",
    "[data-ph-at-id]",
]
TITLE_SELECTORS = [
    "h2",
    "h3",
    "[class*='title']",
    "[class*='position-title']",
]
LOCATION_SELECTORS = [
    "[class*='location']",
    "[class*='city']",
    "[data-testid*='location' i]",
]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _first_selector(page_or_node: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        handle = await page_or_node.query_selector(selector)
        if handle:
            return handle
    return None


async def _extract_text(page_or_node: Any, selectors: list[str]) -> str | None:
    handle = await _first_selector(page_or_node, selectors)
    if not handle:
        return None
    try:
        return _clean(await handle.inner_text())
    except Exception:
        return None


async def _http_api_fast_path(
    board_url: str,
    company: str,
    search_terms: list[str],
    diagnostics: AdapterDiagnostics,
) -> list[DiscoveredJobRecord] | None:
    """Hit the Eightfold /api/apply/v2/jobs REST endpoint directly."""
    import httpx
    from urllib.parse import urlparse

    parsed = urlparse(board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Extract domain from hostname (e.g. hsbc.eightfold.ai -> hsbc.com, or portal.careers.hsbc.com -> hsbc.com)
    # The domain param is usually the company's main domain
    host = parsed.netloc.lower()
    if "eightfold.ai" in host:
        domain = host.split(".eightfold.ai")[0] + ".com"
    else:
        # Custom domain — try extracting from the host
        parts = host.replace("portal.", "").replace("careers.", "").replace("jobs.", "").split(".")
        domain = ".".join(parts[-2:]) if len(parts) >= 2 else host

    api_base = f"{origin}/api/apply/v2/jobs"
    all_records: list[DiscoveredJobRecord] = []
    seen_ids: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # First, verify the API exists with a small test request
            test_resp = await client.get(api_base, params={"domain": domain, "start": 0, "num": 1, "query": "risk"})
            if test_resp.status_code != 200:
                diagnostics.errors.append(f"Eightfold API returned {test_resp.status_code}")
                return None
            test_data = test_resp.json()
            if not isinstance(test_data, dict) or "positions" not in test_data:
                return None

            for term in search_terms:
                start = 0
                while start < 200:  # cap at 200 results per term
                    resp = await client.get(api_base, params={
                        "domain": domain, "start": start, "num": 50, "query": term,
                    })
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    positions = data.get("positions", [])
                    if not positions:
                        break
                    for pos in positions:
                        job_id = str(pos.get("id", ""))
                        if not job_id or job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)

                        title = _clean(pos.get("name") or pos.get("title"))
                        if not title or len(title) < 4:
                            continue
                        location = _clean(pos.get("location"))
                        department = _clean(pos.get("department"))
                        apply_url = pos.get("apply_url") or pos.get("canonicalPositionUrl")
                        if not apply_url:
                            apply_url = f"{origin}/careers/job/{job_id}?domain={domain}"

                        completeness_fields = [title, location, apply_url]
                        completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

                        all_records.append(DiscoveredJobRecord(
                            external_job_id=job_id,
                            title_raw=title,
                            location_raw=location,
                            posted_at_raw=_clean(pos.get("t_create") or pos.get("postedOn")),
                            summary_raw=department,
                            discovered_url=apply_url,
                            apply_url=apply_url,
                            listing_payload=pos,
                            completeness_score=round(completeness_score, 4),
                            extraction_confidence=0.92,
                            provenance={
                                "adapter": "eightfold",
                                "method": ExtractionMethod.BROWSER.value,
                                "company": company,
                                "platform": "Eightfold",
                                "board_url": board_url,
                                "source": "http_api",
                                "search_term": term,
                            },
                        ))

                    total = data.get("count", 0)
                    start += len(positions)
                    if start >= total:
                        break

            diagnostics.counters["http_api_jobs"] = len(all_records)
            return all_records if all_records else None
    except Exception as exc:
        diagnostics.errors.append(f"Eightfold API error: {exc}")
        return None


class EightfoldAdapter(SourceAdapter):
    adapter_name = "eightfold"
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

    async def _search_and_extract(
        self,
        page: Any,
        term: str,
        board: dict[str, Any],
        diagnostics: AdapterDiagnostics,
        settle_ms: int,
    ) -> list[DiscoveredJobRecord]:
        records: list[DiscoveredJobRecord] = []
        search_used = False

        try:
            search_box = await _first_selector(page, SEARCH_INPUT_SELECTORS)
            if search_box:
                search_used = True
                await search_box.click()
                try:
                    await search_box.press("Meta+A")
                except Exception:
                    pass
                await search_box.fill(term)
                await search_box.press("Enter")
                await page.wait_for_timeout(settle_ms)
        except Exception as exc:
            diagnostics.warnings.append(f"Eightfold search input interaction failed for term '{term}': {exc}")

        try:
            cards = await page.query_selector_all(", ".join(CARD_SELECTORS))
        except Exception as exc:
            diagnostics.errors.append(f"Eightfold card query failed for term '{term}': {exc}")
            return []

        diagnostics.counters["cards_seen"] = diagnostics.counters.get("cards_seen", 0) + len(cards)
        if search_used:
            diagnostics.counters["search_terms_applied"] = diagnostics.counters.get("search_terms_applied", 0) + 1
        else:
            diagnostics.counters["search_terms_without_input"] = diagnostics.counters.get("search_terms_without_input", 0) + 1

        for card in cards:
            try:
                title = await _extract_text(card, TITLE_SELECTORS)
                link_el = await card.query_selector("a")
                href = await link_el.get_attribute("href") if link_el else None
                location = await _extract_text(card, LOCATION_SELECTORS)
                if not title:
                    diagnostics.counters["cards_skipped_missing_title"] = diagnostics.counters.get(
                        "cards_skipped_missing_title", 0
                    ) + 1
                    continue

                resolved_url = urljoin(str(board["url"]), href or "") if href else str(board["url"])
                completeness_fields = [title, location, resolved_url]
                completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)
                records.append(
                    DiscoveredJobRecord(
                        external_job_id=resolved_url,
                        title_raw=title,
                        location_raw=location,
                        posted_at_raw=None,
                        summary_raw=None,
                        discovered_url=resolved_url,
                        apply_url=resolved_url,
                        listing_payload={"term": term, "href": href, "search_used": search_used},
                        completeness_score=round(completeness_score, 4),
                        extraction_confidence=0.75 if search_used else 0.68,
                        provenance={
                            "adapter": "eightfold",
                            "method": ExtractionMethod.BROWSER.value,
                            "company": str(board.get("company") or "").strip(),
                            "platform": "Eightfold",
                            "board_url": str(board.get("url") or "").strip(),
                            "search_term": term,
                            "search_used": search_used,
                        },
                    )
                )
            except Exception as exc:
                diagnostics.counters["card_parse_failures"] = diagnostics.counters.get("card_parse_failures", 0) + 1
                diagnostics.warnings.append(f"Eightfold card parse failure for term '{term}': {exc}")
        return records

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("Eightfold source_config requires job_board_url")

        company = str(source_config.get("company") or board_url).strip()
        raw_terms = source_config.get("search_terms") or DEFAULT_SEARCH_TERMS
        search_terms = [str(term).strip() for term in raw_terms if str(term).strip()]
        timeout_ms = int(source_config.get("page_timeout_ms", DEFAULT_PAGE_TIMEOUT_MS))
        settle_ms = int(source_config.get("search_settle_ms", DEFAULT_SEARCH_SETTLE_MS))
        diagnostics = AdapterDiagnostics(
            metadata={
                "board_url": board_url,
                "search_terms": search_terms,
                "page_timeout_ms": timeout_ms,
                "search_settle_ms": settle_ms,
                "since": since.isoformat() if since else None,
                "cursor_ignored": cursor is not None,
            }
        )
        if cursor is not None:
            diagnostics.warnings.append("EightfoldAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append(
                "EightfoldAdapter cannot filter incrementally at source. since was recorded but not enforced."
            )

        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()
        started = time.perf_counter()

        # ── HTTP API fast path ──
        api_records = await _http_api_fast_path(board_url, company, search_terms, diagnostics)
        if api_records:
            all_records = api_records
            seen_urls = {r.discovered_url for r in all_records if r.discovered_url}
            diagnostics.counters["jobs_seen"] = len(all_records)
            diagnostics.counters["unique_urls"] = len(seen_urls)
            diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
            if on_page_scraped:
                await on_page_scraped(1, all_records, all_records)
            return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)

        # ── Browser fallback ──
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    await page.goto(board_url, wait_until="networkidle", timeout=timeout_ms)
                    diagnostics.counters["page_goto_success"] = 1
                    for term in search_terms:
                        term_records = await self._search_and_extract(
                            page,
                            term,
                            {"url": board_url, "company": company},
                            diagnostics,
                            settle_ms,
                        )
                        diagnostics.counters["records_before_dedupe"] = diagnostics.counters.get(
                            "records_before_dedupe", 0
                        ) + len(term_records)
                        records_before = len(all_records)
                        for record in term_records:
                            url = record.discovered_url
                            if not url:
                                diagnostics.counters["records_missing_url"] = diagnostics.counters.get(
                                    "records_missing_url", 0
                                ) + 1
                                continue
                            if url in seen_urls:
                                diagnostics.counters["duplicate_urls"] = diagnostics.counters.get("duplicate_urls", 0) + 1
                                continue
                            seen_urls.add(url)
                            all_records.append(record)
                        if on_page_scraped and len(all_records) > records_before:
                            try:
                                on_page_scraped(search_terms.index(term) + 1, all_records[records_before:], all_records)
                            except Exception:
                                pass
                finally:
                    await page.close()
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            diagnostics.errors.append(f"Eightfold page timeout: {exc}")
            raise
        except PlaywrightError as exc:
            diagnostics.errors.append(f"Eightfold browser failure: {exc}")
            raise

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
