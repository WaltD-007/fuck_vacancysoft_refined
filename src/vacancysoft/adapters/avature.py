"""Avature ATS adapter.

Avature is a widely-used ATS whose public careers sites share a consistent
DOM even across completely different employer tenants:

    <article class="article article--result">
      <div class="article__header__text">
        <h3 class="article__header__text__title">
          <a href=".../careers/JobDetail/<slug>/<id>">{title}</a>
        </h3>
        ...
      </div>
      <!-- Theme A (detail pages, some search pages — e.g. Koch) -->
      <div class="article__content__field">
        <div class="article__content__field__label">Location:</div>
        <div class="article__content__field__value">Wuhan, Hubei</div>
      </div>

      <!-- Theme B (some search result cards — e.g. Ally) -->
      <div class="article__header__text__subtitle">
        <span>Charlotte</span>, <span>NC</span>, <span>USA</span>, ...
      </div>
    </article>

This adapter parses both themes so a single discover() call covers every
active Avature tenant in the DB today (Bloomberg / Carlyle / Ally / Berenberg /
Tesco Insurance / Macquarie — plus Koch if reactivated).

### Transport

Avature tenants fall into two buckets:
  * **Open** (Ally, Bloomberg, Carlyle, ...): respond fully to plain httpx.
  * **Cloudflare-gated** (Koch, and occasionally others): bounce httpx with
    HTTP 202 JS challenge shells. Firefox under Playwright gets through
    cleanly because its TLS fingerprint and HTTP/2 frame ordering differ
    from Chromium's enough that the same Cloudflare configs let it
    through — this was proven empirically by 2026-04-24's Step 4
    archaeology and the initial Firefox backfill in PR #71.

Default transport is therefore Firefox — one shared context across all
pages so startup cost is paid once per scrape. Set ``use_firefox`` to
False in ``source_config`` to force Chromium (faster but hits the 202
wall on Cloudflare-gated tenants).

### Pagination

Avature search URLs follow ``/careers/SearchJobs/<page>`` or
``/careers/SearchJobs?page=<n>`` depending on tenant — some tenants just
scroll-load on the landing page. The adapter tries the ``/<page>`` form
first, falls back to rendering the landing page and parsing whatever
cards are visible. Pagination stops when a page returns zero new
article--result cards.

Default ``max_pages`` is 5 — covers 50-100 jobs per tenant and keeps
runtime reasonable (~40s per tenant headless). Aggregator coverage
(coresignal / adzuna / efc / google_jobs) picks up anything deeper.

### Config

    source_config = {
        "job_board_url":  "https://ally.avature.net/careers",
        "max_pages":      5,       # default
        "use_firefox":    True,    # default (set False for speed on Open tenants)
        "per_page_wait_seconds": 3,  # default — settle time after navigation
    }

### Capabilities

* supports_discovery: True
* supports_browser: True
* supports_pagination: True
* supports_incremental_sync: False (full re-scrape every run; dedupe is
  handled upstream via persist_discovery_batch's existing canonical key).
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.async_api import Browser, BrowserContext, async_playwright

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


_FIREFOX_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:131.0) "
    "Gecko/20100101 Firefox/131.0"
)

# Matches one <article class="article article--result"> element (used for
# search-result cards). Non-greedy, tolerates extra classes via the [^"]* tail.
_ARTICLE_RE = re.compile(
    r'<article[^>]*class="[^"]*\barticle--result\b[^"]*"[^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)

# Title + href. The `article__header__text__title` class wraps an <a> on every
# Avature card we've observed across all tenants.
_TITLE_LINK_RE = re.compile(
    r'class="[^"]*\barticle__header__text__title\b[^"]*"[^>]*>\s*'
    r'<a[^>]*href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.DOTALL | re.IGNORECASE,
)

# Theme A: Location label / value pair inside the card body. Tolerates the
# `view` variant seen on detail pages and the label "Location(s)"/"Location:".
_FIELD_LABEL_VALUE_RE = re.compile(
    r'class="[^"]*\barticle__content(?:__view)?__field__label\b[^"]*"[^>]*>\s*'
    r'([^<]+?)\s*</[^>]+>'
    r'.{0,400}?'
    r'class="[^"]*\barticle__content(?:__view)?__field__value\b[^"]*"[^>]*>\s*'
    r'([^<]+?)\s*</',
    re.DOTALL | re.IGNORECASE,
)

# Theme B: subtitle spans joined by ", ". Ally's search cards use this shape.
_SUBTITLE_BLOCK_RE = re.compile(
    r'class="[^"]*\barticle__header__text__subtitle\b[^"]*"[^>]*>\s*(.*?)\s*</div>',
    re.DOTALL | re.IGNORECASE,
)
_SUBTITLE_SPAN_RE = re.compile(r'<span[^>]*>\s*([^<]+?)\s*</span>', re.DOTALL)

# Labels treated as location when parsing Theme A. Order matters — first hit
# wins. "Work Location(s)" beats bare "City" because it's richer when both
# are present on the same card.
_LOCATION_LABELS: tuple[str, ...] = (
    "work location(s)",
    "work location",
    "job location",
    "location(s)",
    "location",
    "city",
    "office",
)

# Tokens Theme B surfaces that clearly aren't location data — we strip these
# from the subtitle-span join before returning. "Posted" and "Ref #" land in
# the same subtitle block as the city on Ally's layout; "Anticipated
# Application" appears on some job cards.
_SUBTITLE_JUNK_PREFIXES: tuple[str, ...] = (
    "posted",
    "ref #",
    "reference",
    "requisition",
    "anticipated application",
    "closing date",
    "application deadline",
)

# Month names for a span-is-a-date sniff — if the entire span matches this
# shape it's a date, not a location token.
_DATE_SPAN_RE = re.compile(
    r"^[A-Z][a-z]{2}[- ]\d{1,2}[- ]\d{2,4}$|"
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$|"
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}$"
)


def _clean_inline(value: str) -> str:
    """Collapse whitespace and strip trailing/leading punctuation on extracted text."""
    v = re.sub(r"\s+", " ", value).strip()
    return v.strip(".,: -")


def _extract_theme_a_location(card_html: str) -> str | None:
    """Return the first "Location"-like field value, or None."""
    for m in _FIELD_LABEL_VALUE_RE.finditer(card_html):
        label = _clean_inline(m.group(1)).lower().rstrip(":")
        if label in _LOCATION_LABELS and m.group(2).strip():
            return _clean_inline(m.group(2))
    return None


def _extract_theme_b_location(card_html: str) -> str | None:
    """Return the comma-joined subtitle spans (minus junk)."""
    block = _SUBTITLE_BLOCK_RE.search(card_html)
    if not block:
        return None
    spans = [_clean_inline(m.group(1)) for m in _SUBTITLE_SPAN_RE.finditer(block.group(1))]
    kept: list[str] = []
    for s in spans:
        if not s:
            continue
        lowered = s.lower()
        if any(lowered.startswith(p) for p in _SUBTITLE_JUNK_PREFIXES):
            continue
        if _DATE_SPAN_RE.match(s):
            continue
        kept.append(s)
    return ", ".join(kept) if kept else None


def _extract_location(card_html: str) -> str | None:
    return _extract_theme_a_location(card_html) or _extract_theme_b_location(card_html)


def _extract_job_id_from_url(url: str) -> str | None:
    """Avature detail URLs end with /<slug>/<numeric-id>."""
    m = re.search(r"/JobDetail/[^/]+/(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else None


def _parse_cards(html: str, board_url: str, company_name: str) -> list[DiscoveredJobRecord]:
    records: list[DiscoveredJobRecord] = []
    for card_match in _ARTICLE_RE.finditer(html):
        card = card_match.group(1)
        title_match = _TITLE_LINK_RE.search(card)
        if not title_match:
            continue
        href = title_match.group(1).strip()
        title = _clean_inline(title_match.group(2))
        if not title or not href:
            continue
        # Filter out non-job links that sneak into article--result on some
        # tenants — e.g. "Explore all jobs" CTA cards have no JobDetail path.
        if "/JobDetail/" not in href:
            continue
        location = _extract_location(card)
        job_id = _extract_job_id_from_url(href)
        completeness = sum(1 for v in (title, location, href) if v) / 3.0
        records.append(
            DiscoveredJobRecord(
                external_job_id=job_id or href,
                title_raw=title,
                location_raw=location,
                posted_at_raw=None,  # Avature cards rarely surface a date — detail page has it
                summary_raw=None,
                discovered_url=href,
                apply_url=href,
                listing_payload={
                    "source": "avature_adapter",
                    "href": href,
                    "title": title,
                    "location": location,
                    "job_id": job_id,
                },
                completeness_score=round(completeness, 4),
                extraction_confidence=0.90,
                provenance={
                    "adapter": "avature",
                    "method": ExtractionMethod.BROWSER.value,
                    "company": company_name,
                    "platform": "Avature",
                    "board_url": board_url,
                },
            )
        )
    return records


def _build_page_url(board_url: str, page_num: int) -> str:
    """Derive the paginated search URL for a given board URL.

    Observed patterns across tenants:
      * https://{t}.avature.net/careers                       → append /SearchJobs/<n>
      * https://{t}.avature.net/en_US/careers                  → append /SearchJobs/<n>
      * https://{t}.avature.net/careers/SearchJobs             → append /<n>
    """
    parsed = urlparse(board_url.rstrip("/"))
    path = parsed.path
    if not path.endswith("/SearchJobs"):
        path = f"{path}/SearchJobs"
    path = f"{path}/{page_num}"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


class AvatureAdapter(SourceAdapter):
    adapter_name = "avature"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=True,
        supports_incremental_sync=False,
        supports_api=False,
        supports_html=False,
        supports_browser=True,
        supports_site_rescue=False,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("base_url") or "").strip()
        if not board_url:
            raise ValueError("Avature source_config requires job_board_url")

        max_pages = int(source_config.get("max_pages", 5))
        use_firefox = bool(source_config.get("use_firefox", True))
        settle_seconds = float(source_config.get("per_page_wait_seconds", 3.0))
        company_name = lookup_company(
            "avature",
            board_url=board_url,
            explicit_company=source_config.get("company"),
        )

        diagnostics = AdapterDiagnostics(
            metadata={
                "board_url": board_url,
                "max_pages": max_pages,
                "use_firefox": use_firefox,
                "settle_seconds": settle_seconds,
            }
        )

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()

        async with async_playwright() as p:
            browser_type = p.firefox if use_firefox else p.chromium
            browser: Browser = await browser_type.launch(headless=True)
            context: BrowserContext = await browser.new_context(
                user_agent=_FIREFOX_UA if use_firefox else None,
                viewport={"width": 1400, "height": 900},
                locale="en-US",
            )
            try:
                for page_num in range(1, max_pages + 1):
                    page_url = _build_page_url(board_url, page_num) if page_num > 1 else board_url
                    page = await context.new_page()
                    try:
                        try:
                            await page.goto(page_url, wait_until="networkidle", timeout=30000)
                        except Exception:
                            try:
                                await page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                            except Exception:
                                pass
                        await asyncio.sleep(settle_seconds)
                        html = await page.content()
                    except Exception as exc:
                        diagnostics.warnings.append(
                            f"page {page_num} load failed: {type(exc).__name__}: {exc}"
                        )
                        break
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass

                    page_records = _parse_cards(html, board_url, company_name)
                    diagnostics.counters[f"page_{page_num}_cards_raw"] = len(page_records)

                    new_this_page: list[DiscoveredJobRecord] = []
                    for rec in page_records:
                        key = rec.external_job_id or rec.discovered_url or rec.title_raw or ""
                        if not key or key in seen_ids:
                            continue
                        seen_ids.add(key)
                        new_this_page.append(rec)

                    diagnostics.counters[f"page_{page_num}_cards_new"] = len(new_this_page)
                    if not new_this_page:
                        # Either end of results or pagination URL doesn't exist
                        # on this tenant — either way we're done.
                        diagnostics.counters["pages_scraped"] = page_num
                        break
                    all_records.extend(new_this_page)
                    diagnostics.counters["pages_scraped"] = page_num

                    if on_page_scraped:
                        try:
                            await on_page_scraped(page_num, new_this_page, all_records)
                        except Exception:
                            pass
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

        diagnostics.counters["jobs_seen"] = len(all_records)
        diagnostics.counters["unique_ids"] = len(seen_ids)
        return DiscoveryPage(jobs=all_records, next_cursor=None, diagnostics=diagnostics)
