"""Teamtailor adapter — uses the public RSS feed at {board_url}/jobs.rss"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

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

_TT_NS = {"tt": "https://teamtailor.com/locations"}


def _derive_rss_url(board_url: str) -> str:
    """Return the Teamtailor RSS feed URL for a given board URL.

    Handles the three shapes that land in the ``sources.config_blob``
    column for Teamtailor rows:

      1. ``https://<slug>.teamtailor.com/jobs``
         — canonical; strip ``/jobs`` and append ``/jobs.rss``.
      2. ``https://<slug>.teamtailor.com/jobs?split_view=true&query=``
         — URL-bar copy-paste with query params; query string MUST be
         stripped before appending ``/jobs.rss`` or the concatenated
         URL is malformed (server responds with an HTML error page,
         which fails XML parse downstream).
      3. ``https://<slug>.teamtailor.com/#jobs``
         — URL with a JS-router fragment; httpx drops the fragment on
         fetch, but if we naively concatenate ``/jobs.rss`` onto the
         fragment-containing URL we'd form garbage. Stripped the same
         way.

    The 2026-04-21 teamtailor cleanup deactivated 148 cross-adapter
    dupes but left 6 genuine ``*.teamtailor.com`` rows. Two of them
    (GHIB id=420 and IMPOWER id=975) had shape 2 or 3 URLs and
    failed every pipeline run with a ParseError on the HTML error
    response. This helper is the fix — see tests/test_adapters.py.
    """
    parsed = urlparse(board_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/jobs"):
        path = path[:-len("/jobs")]
    cleaned = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return f"{cleaned}/jobs.rss"


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return (el.text or "").strip() or None


def _parse_location(item: ET.Element) -> str | None:
    """Extract city + country from tt:locations."""
    loc = item.find(".//tt:location", _TT_NS)
    if loc is None:
        return None
    city = _text(loc.find("tt:city", _TT_NS))
    country = _text(loc.find("tt:country", _TT_NS))
    if city and country:
        return f"{city}, {country}"
    return city or country


def _parse_item(item: ET.Element, board: dict[str, Any]) -> DiscoveredJobRecord | None:
    title = _text(item.find("title"))
    link = _text(item.find("link"))
    if not title or not link:
        return None

    location = _parse_location(item)
    pub_date = _text(item.find("pubDate"))
    description = _text(item.find("description"))
    department = _text(item.find("tt:department", _TT_NS))
    role_level = _text(item.find("tt:role", _TT_NS))
    remote_status = _text(item.find("remoteStatus"))

    company_name = lookup_company(
        "teamtailor",
        board_url=board.get("url"),
        slug=board.get("slug"),
        explicit_company=board.get("company"),
    )

    # Build a clean summary from department + role level + remote status
    summary_parts = []
    if department:
        summary_parts.append(f"Department: {department}")
    if role_level:
        summary_parts.append(f"Level: {role_level}")
    if remote_status:
        summary_parts.append(f"Remote: {remote_status}")
    summary = " | ".join(summary_parts) if summary_parts else None

    return DiscoveredJobRecord(
        external_job_id=link,
        title_raw=title,
        location_raw=location,
        posted_at_raw=pub_date,
        summary_raw=summary,
        discovered_url=link,
        apply_url=link,
        listing_payload={"description_html": description} if description else None,
        completeness_score=0.8 if location else 0.6,
        extraction_confidence=0.85,
        provenance={
            "adapter": "teamtailor",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "Teamtailor",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
        },
    )


class TeamtailorAdapter(SourceAdapter):
    adapter_name = "teamtailor"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=True,
        supports_html=False,
        supports_browser=False,
        supports_site_rescue=False,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or source_config.get("url") or "").strip()
        if not board_url:
            raise ValueError("TeamtailorAdapter requires job_board_url")

        rss_url = _derive_rss_url(board_url)

        slug = source_config.get("slug")
        company = source_config.get("company")
        board = {"url": board_url, "slug": slug, "company": company}

        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "rss_url": rss_url})
        started = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(rss_url)
                resp.raise_for_status()

            diagnostics.metadata["http_status"] = resp.status_code
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError as parse_exc:
                # Preserve a fingerprint of what Teamtailor actually returned.
                # Without this the error is just "line 54, column 120" with
                # no way to tell if the server returned an HTML error page,
                # a truncated feed, or a genuinely malformed XML payload.
                # See the 2026-04-21 teamtailor triage: the parse errors
                # turned out to be HTML error pages from malformed URLs, not
                # Teamtailor's feed itself.
                snippet = (resp.text or "").strip()[:200].replace("\n", " ")
                diagnostics.metadata["response_snippet"] = snippet
                diagnostics.metadata["content_type"] = resp.headers.get("content-type", "")
                diagnostics.errors.append(
                    f"TeamtailorAdapter ParseError on {rss_url}: {parse_exc}. "
                    f"First 200 chars of response: {snippet!r}"
                )
                raise
            items = root.findall(".//item")
            diagnostics.counters["rss_items"] = len(items)

            records: list[DiscoveredJobRecord] = []
            seen_urls: set[str] = set()

            for item in items:
                rec = _parse_item(item, board)
                if rec and rec.discovered_url not in seen_urls:
                    seen_urls.add(rec.discovered_url)
                    records.append(rec)

            if on_page_scraped and records:
                try:
                    on_page_scraped(1, records, records)
                except Exception:
                    pass

        except Exception as exc:
            diagnostics.errors.append(f"TeamtailorAdapter error: {exc}")
            raise

        diagnostics.counters["jobs_seen"] = len(records)
        diagnostics.counters["unique_urls"] = len(seen_urls)
        diagnostics.timings_ms["discover"] = int((time.perf_counter() - started) * 1000)

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
