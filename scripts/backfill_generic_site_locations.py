#!/usr/bin/env python3
"""One-off backfill — re-extract location from title_raw on existing
generic_site RawJob rows.

Context: PR #50 (2026-04-22) added ``_location_from_title()`` to
generic_browser.py so future scrapes of Goldman / Bupa / etc. pick
up the location that's already embedded in the card's innerText.
This script applies the same parser to existing RawJob rows so we
don't have to wait for the next scheduler pass to land the ~2K
recovery.

Idempotent and safe to re-run:

  * Only processes rows with location_raw IS NULL.
  * Calls _location_from_title(); rows where the parser returns
    None (~13K of the ~15.8K failing rows) are left untouched.
  * When the parser returns a value, writes it to
    RawJob.location_raw AND then calls
    persist_enrichment_for_raw_job() — the canonical enrichment
    path used by the worker — which handles the downstream
    EnrichedJob upsert, geo/agency/title filters, and canonical
    job key computation.

Non-destructive:

  * --dry-run is a MODE, not the default — pass it explicitly to
    preview counts without writing. Default mode commits.
  * No DELETEs, no schema changes.
  * If the extracted location is in a non-allowed country (e.g.
    Goldman's Bengaluru / Hyderabad rows), the enricher correctly
    marks the row filtered — same as a live scrape. Acceptable.

Usage:
    # Preview — NO WRITES
    python3 scripts/backfill_generic_site_locations.py --dry-run --limit 50

    # Backfill across all generic_site sources
    python3 scripts/backfill_generic_site_locations.py

    # Backfill one source at a time (useful for testing)
    python3 scripts/backfill_generic_site_locations.py --source-id 423

    # Backfill a different adapter (unlikely but supported)
    python3 scripts/backfill_generic_site_locations.py --adapter workday

Expected recovery (first run, 2026-04-22 audit state):

    scanned ≈ 15,837 (generic_site rows with location_raw IS NULL)
    extracted ≈ 2,000 (Goldman + Bupa patterns hit)
    enriched ≈ 1,500-1,800 (some extracted rows are non-allowed
                              country — Bengaluru/Hyderabad — and
                              end up marked geo_filtered; that's
                              correct, not a recovery loss)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from vacancysoft.adapters.generic_browser import _location_from_title  # noqa: E402
from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import RawJob, Source  # noqa: E402
from vacancysoft.pipelines.enrichment_persistence import (  # noqa: E402
    persist_enrichment_for_raw_job,
)


def _candidate_query(adapter: str, source_id: int | None, limit: int | None):
    """Build the SQL filter. Only rows that have a title_raw to parse
    AND location_raw IS NULL are candidates."""
    stmt = (
        select(RawJob)
        .join(Source, RawJob.source_id == Source.id)
        .where(Source.adapter_name == adapter)
        .where(RawJob.location_raw.is_(None))
        .where(RawJob.title_raw.is_not(None))
    )
    if source_id is not None:
        stmt = stmt.where(Source.id == source_id)
    # Order by source_id then raw_job.id for reproducible iteration —
    # helps when the operator wants to re-run after a partial run.
    stmt = stmt.order_by(RawJob.source_id, RawJob.id)
    if limit:
        stmt = stmt.limit(limit)
    return stmt


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else "",
    )
    parser.add_argument(
        "--adapter",
        default="generic_site",
        help="Adapter name to backfill (default: generic_site).",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        help="Restrict to one source.id (useful for testing).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Max rows to process (default: all matching).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Commit every N rows (default 100).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts without committing. No DB writes.",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=20,
        help="Under --dry-run, print the first N title→location mappings (default 20).",
    )
    args = parser.parse_args()

    scanned = 0
    extracted = 0
    enriched_ok = 0
    filtered_out = 0
    skipped_no_match = 0

    preview_printed = 0

    with SessionLocal() as s:
        stmt = _candidate_query(args.adapter, args.source_id, args.limit)
        rows = s.execute(stmt).scalars().all()

        total = len(rows)
        print(
            f"Scanning {total} candidate rows "
            f"(adapter={args.adapter!r} "
            f"source_id={args.source_id if args.source_id is not None else '*'} "
            f"dry_run={args.dry_run})…",
            flush=True,
        )
        if total == 0:
            print("  No candidates. Exiting.")
            return 0

        for i, raw in enumerate(rows, start=1):
            scanned += 1
            extracted_loc = _location_from_title(raw.title_raw)
            if not extracted_loc:
                skipped_no_match += 1
                continue
            extracted += 1

            if args.dry_run:
                if preview_printed < args.preview_rows:
                    title_preview = (raw.title_raw or "").replace("\n", " / ")[:80]
                    print(f"  [dry] src#{raw.source_id:>5} {title_preview!r:<82} -> {extracted_loc!r}")
                    preview_printed += 1
                continue

            # Commit path
            raw.location_raw = extracted_loc
            enriched = persist_enrichment_for_raw_job(s, raw)
            if enriched is None:
                # Filtered by geo / agency / title check inside the
                # enricher. Still a successful backfill in the sense
                # that we correctly processed the row; just doesn't
                # end up in the EnrichedJob table as a live lead.
                filtered_out += 1
            else:
                enriched_ok += 1

            if i % args.batch_size == 0:
                s.commit()
                print(
                    f"  …committed batch at row {i}/{total} "
                    f"(extracted={extracted} enriched_ok={enriched_ok} "
                    f"filtered={filtered_out})",
                    flush=True,
                )

        if not args.dry_run:
            s.commit()

    print("\nDone.")
    print(f"  scanned          = {scanned}")
    print(f"  extracted        = {extracted}  (title_raw parsed → location found)")
    print(f"  skipped_no_match = {skipped_no_match}  (title_raw had no embedded location)")
    if args.dry_run:
        print("  (dry-run — no DB writes)")
    else:
        print(f"  enriched_ok      = {enriched_ok}  (landed as live EnrichedJob rows)")
        print(f"  filtered_out     = {filtered_out}  (non-allowed country / recruiter / title — correctly filtered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
