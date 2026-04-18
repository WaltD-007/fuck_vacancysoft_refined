"""
Detail back-fill pipeline step.

Finds enriched jobs that are missing date or location data and attempts to
fetch the real values from individual job detail pages.  Runs after initial
enrichment but before classification/scoring so that downstream steps benefit
from the improved data.

Strategy (fastest → slowest):
  1. Workday CXS API          — JSON endpoint, instant
  2. SmartRecruiters API      — JSON endpoint, instant
  3. Generic HTML fetch       — plain httpx GET + JSON-LD / meta extraction
                                Works for any site with schema.org markup.
                                No browser needed, but may be blocked by JS walls.

The slow Playwright browser fallback is NOT used during pipeline runs to avoid stalling.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from vacancysoft.db.models import EnrichedJob, RawJob
from vacancysoft.enrichers.detail_fetch import (
    _fetch_workday_detail,
    _fetch_smartrecruiters_detail,
    _HTTP_TIMEOUT,
    _extract_location_from_json_ld,
    _extract_location_from_meta,
    _extract_date_from_json_ld,
    _extract_date_from_time_elements,
    _extract_date_from_meta,
)
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import normalise_location, is_allowed_country

logger = logging.getLogger(__name__)

# Domains we can backfill via fast API calls (no browser needed)
_API_DOMAINS = ("myworkdayjobs.com", "myworkdaysite.com", "smartrecruiters.com")


def _has_api_endpoint(url: str | None) -> bool:
    """Return True if the URL belongs to a platform with a fast detail API."""
    if not url:
        return False
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in _API_DOMAINS)


def _backfill_one_sync(
    enriched: EnrichedJob,
    raw_job: RawJob,
    client: httpx.Client,
) -> bool:
    """Fetch detail for one job via synchronous HTTP and update the record."""
    url = raw_job.canonical_url or raw_job.discovered_url
    if not url:
        enriched.detail_fetch_status = "detail_fetched"
        return False

    domain = urlparse(url).netloc.lower()
    detail: dict[str, str | None] = {"date": None, "location": None}

    try:
        if "myworkdayjobs.com" in domain or "myworkdaysite.com" in domain:
            detail = _fetch_workday_detail_sync(url, client)
        elif "smartrecruiters.com" in domain:
            detail = _fetch_smartrecruiters_detail_sync(url, client)
        else:
            # Generic fallback: parse JSON-LD / meta tags from the job page HTML
            detail = _fetch_generic_html_detail(url, client)
    except Exception as exc:
        logger.debug("Detail API call failed for %s: %s", url, exc)
        enriched.detail_fetch_status = "detail_failed"
        return False

    updated = False

    # Back-fill date if we got one and it's currently missing
    if detail.get("date") and enriched.posted_at is None:
        parsed = parse_posted_date(detail["date"])
        if parsed:
            enriched.posted_at = parsed
            enriched.freshness_bucket = "recent"
            updated = True

    # Back-fill location if we got a better one
    if detail.get("location"):
        new_loc = normalise_location(detail["location"])
        new_country = new_loc.get("country")
        new_city = new_loc.get("city")

        has_better_country = new_country and not enriched.location_country
        has_better_city = new_city and not enriched.location_city

        if has_better_country or has_better_city:
            if new_country:
                enriched.location_country = new_country
            if new_city:
                enriched.location_city = new_city
            if new_loc.get("region"):
                enriched.location_region = new_loc["region"]
            enriched.location_text = detail["location"]
            updated = True

            # Re-check geo filter with the new country
            if not is_allowed_country(enriched.location_country):
                enriched.detail_fetch_status = "geo_filtered"
                return True

    enriched.detail_fetch_status = "detail_fetched"
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous API wrappers (no asyncio needed — just plain httpx)
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_workday_detail_sync(url: str, client: httpx.Client) -> dict:
    from vacancysoft.enrichers.detail_fetch import _workday_api_url

    result: dict[str, str | None] = {"date": None, "location": None}
    api_url = _workday_api_url(url)
    if not api_url:
        return result

    resp = client.get(api_url, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        return result
    data = resp.json()
    info = data.get("jobPostingInfo") or data

    raw_date = info.get("startDate") or info.get("postedOn") or ""
    if raw_date:
        result["date"] = raw_date

    locs = info.get("locations") or []
    if locs:
        result["location"] = ", ".join(
            loc.get("name", "") for loc in locs if loc.get("name")
        )
    elif info.get("location"):
        result["location"] = info["location"]

    return result


def _fetch_generic_html_detail(url: str, client: httpx.Client) -> dict:
    """
    Fetch a job page via plain httpx and extract location/date from HTML.

    Works for any site that includes schema.org JSON-LD or standard meta tags —
    no browser required.  Returns empty strings if the page is JS-walled or
    the extraction finds nothing useful.
    """
    result: dict[str, str | None] = {"date": None, "location": None}
    try:
        resp = client.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        )
        if resp.status_code != 200:
            return result
        html = resp.text
    except Exception as exc:
        logger.debug("Generic HTML fetch failed for %s: %s", url, exc)
        return result

    for extractor in [_extract_date_from_json_ld, _extract_date_from_time_elements, _extract_date_from_meta]:
        val = extractor(html)
        if val:
            result["date"] = val
            break

    for extractor in [_extract_location_from_json_ld, _extract_location_from_meta]:
        val = extractor(html)
        if val:
            result["location"] = val
            break

    return result


def _fetch_smartrecruiters_detail_sync(url: str, client: httpx.Client) -> dict:
    result: dict[str, str | None] = {"date": None, "location": None}
    path_parts = urlparse(url).path.strip("/").split("/")
    if len(path_parts) < 2:
        return result
    slug = path_parts[1]
    job_id = slug.split("-")[0]
    company = path_parts[0]

    api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}"
    resp = client.get(api_url, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        return result
    data = resp.json()

    if data.get("releasedDate"):
        result["date"] = data["releasedDate"]

    loc = data.get("location") or {}
    parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
    loc_str = ", ".join(p for p in parts if p)
    if loc_str:
        result["location"] = loc_str

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Main pipeline entry point
# ──────────────────────────────────────────────────────────────────────────────

def backfill_detail_for_enriched_jobs(
    session: Session,
    limit: int | None = 100,
    concurrency: int = 5,
) -> int:
    """
    Find enriched jobs needing detail back-fill (Workday + SmartRecruiters only)
    and fetch missing data via fast API calls.

    Returns the number of jobs updated.
    """
    # Find enriched jobs that need backfill and have a URL we can hit via API
    stmt = (
        select(EnrichedJob, RawJob)
        .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
        .where(
            EnrichedJob.detail_fetch_status.in_(("enriched",)),
        )
        .where(
            or_(
                EnrichedJob.posted_at.is_(None),
                EnrichedJob.location_country.is_(None),
                EnrichedJob.location_city.is_(None),
            )
        )
        .order_by(EnrichedJob.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = list(session.execute(stmt).all())
    if not rows:
        return 0

    # Include all jobs that have a URL (API or generic HTML fallback)
    backfill_jobs = [
        (enriched, raw_job)
        for enriched, raw_job in rows
        if (raw_job.canonical_url or raw_job.discovered_url)
    ]

    if not backfill_jobs:
        return 0

    # Per-domain rate limiting: track last request time per domain
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    domain_last_request: dict[str, float] = {}
    min_domain_interval = 1.0  # seconds between requests to the same domain
    updated = 0
    failed = 0

    def _rate_limited_backfill(
        enriched: EnrichedJob,
        raw_job: RawJob,
        client: httpx.Client,
    ) -> bool:
        url = raw_job.canonical_url or raw_job.discovered_url or ""
        domain = urlparse(url).netloc.lower()

        # Per-domain rate limiting
        now = time.monotonic()
        last = domain_last_request.get(domain, 0.0)
        wait = max(0, min_domain_interval - (now - last))
        if wait > 0:
            time.sleep(wait)
        domain_last_request[domain] = time.monotonic()

        return _backfill_one_sync(enriched, raw_job, client)

    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        if concurrency <= 1:
            # Sequential with rate limiting
            for enriched, raw_job in backfill_jobs:
                try:
                    if _rate_limited_backfill(enriched, raw_job, client):
                        updated += 1
                except Exception as exc:
                    logger.warning(
                        "Detail fetch failed for %s: %s",
                        raw_job.canonical_url or raw_job.discovered_url,
                        exc,
                    )
                    enriched.detail_fetch_status = "detail_failed"
                    failed += 1
        else:
            # Concurrent with per-domain rate limiting
            # Group by domain so we don't hammer the same host
            from collections import defaultdict as _dd
            by_domain: dict[str, list] = _dd(list)
            for enriched, raw_job in backfill_jobs:
                url = raw_job.canonical_url or raw_job.discovered_url or ""
                domain = urlparse(url).netloc.lower()
                by_domain[domain].append((enriched, raw_job))

            # Process domains in parallel, jobs within each domain sequentially
            def _process_domain(domain_jobs: list) -> tuple[int, int]:
                _updated = 0
                _failed = 0
                for enriched, raw_job in domain_jobs:
                    try:
                        if _rate_limited_backfill(enriched, raw_job, client):
                            _updated += 1
                    except Exception as exc:
                        logger.warning(
                            "Detail fetch failed for %s: %s",
                            raw_job.canonical_url or raw_job.discovered_url,
                            exc,
                        )
                        enriched.detail_fetch_status = "detail_failed"
                        _failed += 1
                return _updated, _failed

            with ThreadPoolExecutor(max_workers=min(concurrency, len(by_domain))) as pool:
                futures = {
                    pool.submit(_process_domain, jobs): domain
                    for domain, jobs in by_domain.items()
                }
                for future in as_completed(futures):
                    u, f = future.result()
                    updated += u
                    failed += f

    logger.info("Detail backfill: %d updated, %d failed out of %d", updated, failed, len(backfill_jobs))
    session.commit()
    return updated
