#!/usr/bin/env python3
"""Re-evaluate filtered-stub EnrichedJob rows under current enricher logic.

Context: ``enrich_raw_jobs()`` in the canonical pipeline uses a
``NOT EXISTS`` guard on EnrichedJob.raw_job_id — any RawJob that
already has an EnrichedJob row (even a filtered-stub one written
by ``_mark_filtered``) is skipped on subsequent runs. That means a
row filtered under old logic — missing location, wrong country,
etc. — stays filtered forever, even after shipping:

  * PR #47 — enricher-side fixes (German (Kreis), tri-part ISO,
    UK-known-towns fallback, multi-site sentinels, native
    country names like "Deutschland")
  * PR #50 — generic_site title_raw fallback
  * PR #52 — Slice A1 filter (language-switcher / pagination)

This script forces a re-evaluation by calling
``persist_enrichment_for_raw_job()`` directly (which upserts
whether or not an EnrichedJob already exists). For each targeted
row, the possible outcomes are:

  1. **Stayed filtered, same reason** — old + new logic agree the row
     is out of scope (truly non-allowed country, recruiter, etc.).
  2. **Stayed filtered, different reason** — location now resolves
     differently but still trips a downstream filter.
  3. **Became live (enriched)** — the new logic resolved it cleanly.
     These rows need classification + scoring to appear as leads.
  4. **Newly filtered** (wasn't a stub before, gained one). Rare.

When ``--commit`` is passed, the script also runs the classifier
and scorer on any rows that transitioned to live, so the recovery
actually shows up in the leads feed without waiting for a full
``prospero pipeline run``.

SAFETY

  * Default mode is DRY-RUN. You must pass ``--commit`` to write.
  * Do NOT run this concurrently with a live scrape pipeline —
    race conditions between upserting the same EnrichedJob row
    are unlikely but not impossible.
  * --filter-reasons selects which stub statuses to reprocess.
    Default: ``geo_filtered`` only (the category shown by the
    audit to have the most recoverable rows). Add
    ``title_filtered`` or ``agency_filtered`` if you want them,
    but these rarely change outcome.
  * --include-null-country additionally targets LIVE EnrichedJob
    rows where ``location_country IS NULL`` — these are rows that
    the enricher kept (because ``is_allowed_country(None) = True``)
    but that have no country resolved. New logic may now resolve
    a country. Conservative default: off (targets stubs only).

USAGE

    # Preview — no writes
    python3 scripts/reenrich_filtered_stubs.py --dry-run

    # Preview including null-country live rows
    python3 scripts/reenrich_filtered_stubs.py --dry-run --include-null-country

    # Commit. Runs re-enrich + classify + score.
    python3 scripts/reenrich_filtered_stubs.py --commit

    # Scoped to one adapter (useful for testing)
    python3 scripts/reenrich_filtered_stubs.py --adapter generic_site --commit

ROLLBACK

The script doesn't delete rows — it calls persist_enrichment_for_raw_job
which upserts. Rows that stayed filtered keep their stub (possibly with
a different reason). Rows that became live get a real EnrichedJob row.

If the re-evaluation produces bad results (e.g. the new logic is wrong
and mis-classifies), the rollback path is a DB restore from
``.data/backups/prospero-<date>.db`` — same rollback model as the
cleanup script in the step-5 handoff.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import or_, select  # noqa: E402

from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import EnrichedJob, RawJob, Source  # noqa: E402
from vacancysoft.pipelines.classification_persistence import (  # noqa: E402
    classify_enriched_jobs,
)
from vacancysoft.pipelines.enrichment_persistence import (  # noqa: E402
    persist_enrichment_for_raw_job,
)
from vacancysoft.pipelines.scoring_persistence import (  # noqa: E402
    score_enriched_jobs,
)


def _classify_outcome(
    before_status: str | None,
    before_country: str | None,
    after_enriched: EnrichedJob | None,
) -> str:
    """Label the before→after transition for reporting."""
    if after_enriched is None:
        # persist_enrichment_for_raw_job returned None → marked filtered.
        # Without an extra round-trip to the DB we can't tell which filter
        # fired this time; the row now has SOME stub. Lump as
        # "stayed_or_became_filtered" — good enough for the summary.
        if before_status == "enriched":
            return "was_live_now_filtered"
        return "stayed_filtered"
    # persist returned a real EnrichedJob row
    if before_status == "enriched":
        # Was already live, re-evaluation touched it (probably a country
        # change under new logic)
        if before_country != after_enriched.location_country:
            return "live_country_changed"
        return "live_unchanged"
    # Was a stub of some kind; now live
    return "became_live"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else "",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually re-enrich. Without this, dry-run only.",
    )
    parser.add_argument(
        "--adapter",
        help="Restrict to one adapter (e.g. 'generic_site'). Default: all adapters.",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        help="Restrict to one Source.id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Max rows to process.",
    )
    parser.add_argument(
        "--filter-reasons",
        default="geo_filtered",
        help=(
            "Comma-separated detail_fetch_status values to target. "
            "Default: geo_filtered. Other options: title_filtered, agency_filtered."
        ),
    )
    parser.add_argument(
        "--include-null-country",
        action="store_true",
        help=(
            "Also target live EnrichedJob rows (detail_fetch_status='enriched') "
            "where location_country IS NULL."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Commit every N rows (default 100).",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=20,
        help="Under --dry-run, print first N targeted rows (default 20).",
    )
    args = parser.parse_args()

    target_statuses = [s.strip() for s in args.filter_reasons.split(",") if s.strip()]

    # Build the candidate query. Join EnrichedJob → RawJob → Source so we can
    # filter by adapter + capture the needed context in one round trip.
    with SessionLocal() as s:
        conditions = [EnrichedJob.detail_fetch_status.in_(target_statuses)]
        if args.include_null_country:
            conditions = [
                or_(
                    EnrichedJob.detail_fetch_status.in_(target_statuses),
                    (EnrichedJob.detail_fetch_status == "enriched")
                    & EnrichedJob.location_country.is_(None),
                )
            ]

        stmt = (
            select(EnrichedJob, RawJob)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(*conditions)
        )
        if args.adapter:
            stmt = stmt.where(Source.adapter_name == args.adapter)
        if args.source_id is not None:
            stmt = stmt.where(Source.id == args.source_id)
        # Deterministic iteration order
        stmt = stmt.order_by(RawJob.source_id, RawJob.id)
        if args.limit:
            stmt = stmt.limit(args.limit)

        pairs = s.execute(stmt).all()
        total = len(pairs)

        print(
            f"Targeting {total} EnrichedJob rows "
            f"(reasons={target_statuses} "
            f"include_null_country={args.include_null_country} "
            f"adapter={args.adapter or '*'} "
            f"source_id={args.source_id if args.source_id is not None else '*'} "
            f"dry_run={not args.commit})"
        )
        if total == 0:
            print("  No candidates. Exiting.")
            return 0

        # ── Dry-run branch ────────────────────────────────────────
        if not args.commit:
            # Break down by detail_fetch_status + adapter
            status_adapter: Counter = Counter()
            for ej, rj in pairs:
                src = s.execute(
                    select(Source).where(Source.id == rj.source_id)
                ).scalar_one_or_none()
                status_adapter[(ej.detail_fetch_status, src.adapter_name if src else "?")] += 1

            print("\nBreakdown by (status, adapter):")
            for (status, adapter), cnt in sorted(status_adapter.items(), key=lambda t: -t[1]):
                print(f"  {status:<20} {adapter:<22} {cnt}")

            print(f"\nFirst {min(args.preview_rows, total)} targeted rows:")
            for ej, rj in pairs[:args.preview_rows]:
                loc_preview = (rj.location_raw or "<NULL>")[:40]
                title_preview = (rj.title_raw or "<NULL>")[:50]
                print(
                    f"  src#{rj.source_id:>5} "
                    f"status={ej.detail_fetch_status:<18} "
                    f"old_country={(ej.location_country or 'NULL'):<22} "
                    f"loc_raw={loc_preview!r:<42} "
                    f"title={title_preview!r}"
                )

            print("\n(dry-run — pass --commit to actually re-enrich)")
            return 0

        # ── Commit branch ─────────────────────────────────────────
        outcomes: Counter = Counter()
        print()
        for i, (ej, rj) in enumerate(pairs, start=1):
            before_status = ej.detail_fetch_status
            before_country = ej.location_country

            result = persist_enrichment_for_raw_job(s, rj)
            # persist may mark_filtered (returns None) or upsert an
            # 'enriched' row. The passed-in `ej` object reference is
            # the SAME row either way (upserted) — so it now holds
            # the post-state.
            outcomes[_classify_outcome(before_status, before_country, result)] += 1

            if i % args.batch_size == 0:
                s.commit()
                print(
                    f"  …committed batch {i}/{total} "
                    f"(became_live={outcomes['became_live']} "
                    f"stayed={outcomes['stayed_filtered']} "
                    f"country_changed={outcomes['live_country_changed']})"
                )

        s.commit()

        print("\nRe-enrichment complete.")
        for outcome, cnt in sorted(outcomes.items(), key=lambda t: -t[1]):
            print(f"  {outcome:<30} {cnt}")

        newly_live = outcomes["became_live"] + outcomes["live_country_changed"]
        if newly_live == 0:
            print("\nNo rows transitioned to a live state — skipping classify+score.")
            return 0

        # ── Follow-up: classify + score the newly-live rows ───────
        # classify_enriched_jobs / score_enriched_jobs have their own
        # NOT-EXISTS-style guards — they pick up only rows that need
        # processing, so calling them globally is cheap.
        print(f"\n{newly_live} rows transitioned to live. Running classifier + scorer…")

        classified = classify_enriched_jobs(s, limit=None)
        print(f"  classified: {classified}")
        scored = score_enriched_jobs(s, limit=None)
        print(f"  scored:     {scored}")

        print("\nDone. Run the audit to confirm:")
        print("  python3 scripts/audit_adapter_locations.py --adapter generic_site --only-failing \\")
        print("      --out artifacts/generic_site-failing-post-reenrich.xlsx")

    return 0


if __name__ == "__main__":
    sys.exit(main())
