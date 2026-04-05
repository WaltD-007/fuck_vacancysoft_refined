from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    SourceAdapter,
)

DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats"]
DEFAULT_PAGE_TIMEOUT_MS = 60_000


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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

    async def _search_and_extract(self, page: Any, term: str, board: dict[str, Any]) -> list[DiscoveredJobRecord]:
        records: list[DiscoveredJobRecord] = []
        try:
            search_box = await page.query_selector(
                "input[type='text'], input[placeholder*='search' i], input[placeholder*='job' i]"
            )
            if search_box:
                await search_box.click()
                await search_box.press("Meta+A")
                await search_box.fill(term)
                await search_box.press("Enter")
                await asyncio.sleep(3)

            cards = await page.query_selector_all(
                "[class*='position'], [class*='job-card'], [class*='jobcard'], article, [data-ph-at-id]"
            )
            for card in cards:
                try:
                    title_el = await card.query_selector(
                        "h2, h3, [class*='title'], [class*='position-title']"
                    )
                    link_el = await card.query_selector("a")
                    loc_el = await card.query_selector("[class*='location'], [class*='city']")

                    title = _clean(await title_el.inner_text()) if title_el else None
                    href = await link_el.get_attribute("href") if link_el else None
                    location = _clean(await loc_el.inner_text()) if loc_el else None
                    if not title:
                        continue
                    resolved_url = urljoin(str(board["url"]), href or "") if href else str(board["url"])
                    completeness_score = sum(1 for value in [title, location, resolved_url] if value) / 3
                    records.append(
                        DiscoveredJobRecord(
                            external_job_id=resolved_url,
                            title_raw=title,
                            location_raw=location,
                            posted_at_raw=None,
                            summary_raw=None,
                            discovered_url=resolved_url,
                            apply_url=resolved_url,
                            listing_payload={"term": term, "href": href},
                            completeness_score=round(completeness_score, 4),
                            extraction_confidence=0.75,
                            provenance={
                                "adapter": "eightfold",
                                "method": ExtractionMethod.BROWSER.value,
                                "company": str(board.get("company") or ""),
                                "platform": "Eightfold",
                                "board_url": str(board.get("url") or ""),
                                "search_term": term,
                            },
                        )
                    )
                except Exception:
                    continue
        except Exception:
            return records
        return records

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("Eightfold source_config requires job_board_url")
        company = str(source_config.get("company") or board_url).strip()
        raw_terms = source_config.get("search_terms") or DEFAULT_SEARCH_TERMS
        search_terms = [str(term).strip() for term in raw_terms if str(term).strip()]
        timeout_ms = int(source_config.get("page_timeout_ms", DEFAULT_PAGE_TIMEOUT_MS))
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "search_terms": search_terms})
        all_records: list[DiscoveredJobRecord] = []
        seen_urls: set[str] = set()

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(board_url, wait_until="networkidle", timeout=timeout_ms)
                for term in search_terms:
                    term_records = await self._search_and_extract(page, term, {"url": board_url, "company": company})
                    for record in term_records:
                        if record.discovered_url and record.discovered_url not in seen_urls:
                            seen_urls.add(record.discovered_url)
                            all_records.append(record)
                    await asyncio.sleep(random.uniform(1.0, 2.0))
            finally:
                await page.close()
                await context.close()
                await browser.close()

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
