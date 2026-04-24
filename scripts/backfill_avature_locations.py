#!/usr/bin/env python3
"""One-off backfill — fetch location + posted-date for RawJob rows whose
URL points at an Avature-hosted careers board (``*.avature.net`` plus
``recruitment.macquarie.com`` and other white-labelled tenants detected
by the 2026-04-24 archaeology pass).

### Context

Avature is a widely-used ATS whose job-detail pages have a consistent,
server-rendered structure:

    <div class="article__content__view__field">
      <div class="article__content__view__field__label">Work Location(s)</div>
      <div class="article__content__view__field__value">Charlotte, NC</div>
    </div>

A handful of Avature tenants (Koch, Macquarie) are fronted by Cloudflare
and return HTTP 202 to plain ``curl``/``httpx`` — those rows are
reported but skipped by v1 of this script. A follow-up can add a
Playwright fallback when operator pain justifies the runtime cost.

### What it fixes

Generic_site scraping gets the URL and title fine for Avature tenants
but misses the location because the card on the search results page
puts it in a non-standard node. This script loads each JOB-DETAIL page
directly and extracts location + posted-date from the canonical Avature
pattern above.

### Recovery estimate (from 2026-04-24 archaeology)

| Tenant (source) | Rows | Path           |
|-----------------|-----:|----------------|
| Ally Invest     |  258 | httpx-direct ✅ |
| Koch Industries | 6029 | needs Playwright (skipped here) |
| Macquarie       |  544 | TBD (Cloudflare status unknown)  |

First-pass httpx run is expected to recover Ally plus any other
Avature tenants across the DB that respond without a Cloudflare
challenge. Report at the end names the blocked tenants for follow-up.

### Safety

  * Idempotent. Only rows with ``location_raw IS NULL`` are touched.
  * Default is COMMIT (matches ``backfill_generic_site_locations.py``).
    Pass ``--dry-run`` to preview counts without writing.
  * Rate-limited (``--delay`` seconds between requests) to avoid
    accidentally tripping a tenant's WAF mid-sweep.
  * Per-tenant request budget (``--per-tenant-limit``) caps damage if a
    misbehaving pattern keeps writing wrong values.

### Usage

    # Preview — no writes
    python3 scripts/backfill_avature_locations.py --dry-run --limit 20

    # Full run (sequential, polite rate)
    python3 scripts/backfill_avature_locations.py

    # Scope to one tenant
    python3 scripts/backfill_avature_locations.py --host-substring ally.avature.net
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import httpx  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import RawJob, Source  # noqa: E402
from vacancysoft.pipelines.enrichment_persistence import (  # noqa: E402
    persist_enrichment_for_raw_job,
)


# Host patterns treated as Avature. Public tenants all use *.avature.net;
# white-labels (recruitment.macquarie.com) get added here as we find them.
_AVATURE_HOST_PATTERNS: tuple[str, ...] = (
    ".avature.net",
    "recruitment.macquarie.com",
)

# Labels (case-insensitive, trailing punctuation stripped) whose value we
# treat as a location. Ordered by preference — "Work Location(s)" beats
# "City" when both are present because it's usually richer.
_LOCATION_LABELS: tuple[str, ...] = (
    "work location(s)",
    "work location",
    "job location",
    "location(s)",
    "location",
    "city",
    "office",
)

# Realistic browser headers. Avature tenants that DO respond to httpx
# still sometimes bounce requests that look too much like curl.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _is_avature_url(url: Optional[str]) -> bool:
    if not url:
        return False
    lower = url.lower()
    return any(p in lower for p in _AVATURE_HOST_PATTERNS)


_LABEL_VALUE_PATTERN = re.compile(
    r'class="article__content(?:__view)?__field__label[^"]*"[^>]*>\s*'
    r'([^<]+?)\s*</[^>]+>'
    r'.{0,400}?'
    r'class="article__content(?:__view)?__field__value[^"]*"[^>]*>\s*'
    r'([^<]+?)\s*</',
    re.DOTALL,
)


def _extract_avature_fields(html: str) -> dict[str, str]:
    """Return all label → value pairs from Avature's detail-page DOM."""
    fields: dict[str, str] = {}
    for match in _LABEL_VALUE_PATTERN.finditer(html):
        label = re.sub(r"[:\s]+$", "", match.group(1).strip()).lower()
        value = match.group(2).strip()
        if not label or not value:
            continue
        fields.setdefault(label, value)
    return fields


def _pick_location(fields: dict[str, str]) -> Optional[str]:
    for candidate in _LOCATION_LABELS:
        value = fields.get(candidate)
        if value:
            return value
    return None


def _fetch(url: str, timeout: float) -> tuple[int, str, int]:
    """Return (status_code, body, content_length). Never raises."""
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = client.get(url)
            return resp.status_code, resp.text, len(resp.content)
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}: {exc}", 0


def _tenant_from_url(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else "?"


def _candidate_query(session, host_substring: Optional[str], limit: Optional[int]):
    # Only look at URLs that match the Avature job-DETAIL path. Generic_browser
    # also harvests `/careers/ApplicationMethods?...` and `/_linkedinApiv2?...`
    # URLs as "jobs" — those aren't detail pages, they'll never have a
    # "Work Location" field, and fetching them wastes budget and pollutes the
    # "no_location_found" counter.
    stmt = (
        select(RawJob, Source)
        .join(Source, RawJob.source_id == Source.id)
        .where(RawJob.location_raw.is_(None))
        .where(RawJob.discovered_url.is_not(None))
        .where(RawJob.discovered_url.ilike("%/JobDetail/%"))
        .order_by(Source.id, RawJob.id)
    )
    if host_substring:
        stmt = stmt.where(RawJob.discovered_url.ilike(f"%{host_substring}%"))
    else:
        # Generic "looks like Avature" filter — any of the known host patterns
        from sqlalchemy import or_
        clauses = [RawJob.discovered_url.ilike(f"%{p}%") for p in _AVATURE_HOST_PATTERNS]
        stmt = stmt.where(or_(*clauses))
    if limit:
        stmt = stmt.limit(limit)
    return stmt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="Preview only (no writes).")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process.")
    parser.add_argument("--host-substring", help="Restrict to URLs containing this host string.")
    parser.add_argument("--delay", type=float, default=0.4, help="Seconds between requests. Default 0.4.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout seconds.")
    parser.add_argument("--per-tenant-limit", type=int, default=0, help="Cap rows processed per tenant (0 = no cap).")
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "COMMIT"
    print(f"Avature backfill — {mode}")
    print(f"  host_substring={args.host_substring or '<any avature>'}  limit={args.limit}  delay={args.delay}s")
    print()

    stats = {
        "scanned": 0, "fetched_ok": 0, "extracted": 0, "wrote": 0,
        "blocked_202": 0, "blocked_403": 0, "fetch_failed": 0,
        "no_location_found": 0, "skipped_tenant_cap": 0,
    }
    per_tenant_processed: dict[str, int] = {}
    per_tenant_recovered: dict[str, int] = {}
    per_tenant_blocked: dict[str, int] = {}

    with SessionLocal() as session:
        rows = session.execute(_candidate_query(session, args.host_substring, args.limit)).all()
        print(f"Found {len(rows)} candidate RawJob rows\n")

        for raw_job, source in rows:
            stats["scanned"] += 1
            tenant = _tenant_from_url(raw_job.discovered_url)

            if args.per_tenant_limit and per_tenant_processed.get(tenant, 0) >= args.per_tenant_limit:
                stats["skipped_tenant_cap"] += 1
                continue
            per_tenant_processed[tenant] = per_tenant_processed.get(tenant, 0) + 1

            url = raw_job.discovered_url
            status, body, _size = _fetch(url, args.timeout)
            if status == 200:
                stats["fetched_ok"] += 1
            elif status == 202:
                stats["blocked_202"] += 1
                per_tenant_blocked[tenant] = per_tenant_blocked.get(tenant, 0) + 1
                if stats["scanned"] <= 5 or stats["scanned"] % 200 == 0:
                    print(f"  blocked 202  {tenant}  {url[:80]}")
                continue
            elif status == 403:
                stats["blocked_403"] += 1
                per_tenant_blocked[tenant] = per_tenant_blocked.get(tenant, 0) + 1
                continue
            else:
                stats["fetch_failed"] += 1
                continue

            fields = _extract_avature_fields(body)
            location = _pick_location(fields)
            if not location:
                stats["no_location_found"] += 1
                continue
            stats["extracted"] += 1

            # Also pick up posted date if present — "Posted Date" field is
            # common enough to be worth grabbing while we're in the DOM.
            posted = fields.get("posted date") or fields.get("posting date")

            if args.dry_run:
                if stats["extracted"] <= 10:
                    print(f"  would-write  {tenant:<22}  loc={location!r}  date={posted!r}")
            else:
                raw_job.location_raw = location
                if posted and not raw_job.posted_at_raw:
                    raw_job.posted_at_raw = posted
                session.flush()
                # Re-run enrichment so EnrichedJob picks up the new location
                try:
                    persist_enrichment_for_raw_job(session=session, raw_job=raw_job)
                except Exception as exc:
                    print(f"  WARN enrichment failed for raw#{raw_job.id}: {exc}")
                stats["wrote"] += 1
                per_tenant_recovered[tenant] = per_tenant_recovered.get(tenant, 0) + 1
                if stats["wrote"] % 50 == 0:
                    session.commit()
                    print(f"  committed batch — wrote {stats['wrote']} so far")

            if args.delay > 0:
                time.sleep(args.delay)

        if not args.dry_run:
            session.commit()

    print()
    print("=== Summary ===")
    for k, v in stats.items():
        print(f"  {k:<22} {v}")

    if per_tenant_blocked:
        print("\n=== Cloudflare-blocked tenants (need Playwright fallback in a follow-up) ===")
        for tenant, count in sorted(per_tenant_blocked.items(), key=lambda t: -t[1]):
            print(f"  {tenant:<40}  {count} blocked")

    if per_tenant_recovered:
        print("\n=== Successful tenants ===")
        for tenant, count in sorted(per_tenant_recovered.items(), key=lambda t: -t[1]):
            print(f"  {tenant:<40}  {count} recovered")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
