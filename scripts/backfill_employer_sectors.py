"""Backfill ``enriched_jobs.employer_sector`` for all existing rows.

Resolves the per-lead employer the same way enrichment_persistence does:

1. ``EnrichedJob.team`` (set at enrichment time from the listing payload
   for aggregator leads) — this is the aggregator's resolved company.
2. Else fall back to the source row's ``employer_name``.

Then runs ``detect_sector(resolved_employer)`` to pick the sector and
writes it to ``enriched_jobs.employer_sector``. Idempotent.

Default mode is dry-run; pass ``--commit`` to apply.
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select, update

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, Source
from vacancysoft.source_registry.sector_classifier import (
    detect_sector,
    invalidate_cache,
)


_BATCH_SIZE = 500


def _run(commit: bool) -> None:
    invalidate_cache()
    by_sector_after: Counter[str] = Counter()
    updates: list[tuple[str, str, str]] = []  # (enriched_id, old, new)

    with SessionLocal() as session:
        # Single SELECT joining EnrichedJob → RawJob → Source so we can
        # resolve the employer in one pass. ``employer_sector`` is the
        # current DB value (defaults to 'unknown' for old rows).
        rows = list(session.execute(
            select(
                EnrichedJob.id,
                EnrichedJob.team,
                EnrichedJob.employer_sector,
                Source.employer_name,
            )
            .select_from(EnrichedJob)
            .join(RawJob, RawJob.id == EnrichedJob.raw_job_id)
            .join(Source, Source.id == RawJob.source_id)
        ).all())

        print(f"Enriched jobs scanned: {len(rows)}")

        for ej_id, team, current_sector, src_name in rows:
            resolved = team or src_name or ""
            new_sector = detect_sector(resolved, "", "")
            by_sector_after[new_sector] += 1
            if (current_sector or "unknown") != new_sector:
                updates.append((ej_id, current_sector or "unknown", new_sector))

        print(f"Updates needed: {len(updates)}")
        print()
        print("Final distribution by employer_sector (after this run):")
        for sector, n in by_sector_after.most_common():
            print(f"  {sector:<24} {n}")
        print()

        if not updates:
            print("No changes — already in sync.")
            return

        # Sample
        print("Sample (first 10):")
        for ej_id, old, new in updates[:10]:
            print(f"  ej#{ej_id[:8]}  {old} → {new}")
        if len(updates) > 10:
            print(f"  ... ({len(updates) - 10} more)")
        print()

        if not commit:
            print("Dry run — pass --commit to apply.")
            return

        # Batch the UPDATEs to keep transaction time bounded.
        for i in range(0, len(updates), _BATCH_SIZE):
            batch = updates[i:i + _BATCH_SIZE]
            for ej_id, _, new in batch:
                session.execute(
                    update(EnrichedJob)
                    .where(EnrichedJob.id == ej_id)
                    .values(employer_sector=new)
                )
            session.commit()
            print(f"  committed batch {i // _BATCH_SIZE + 1} ({len(batch)} rows)")
        print(f"\nTotal updates committed: {len(updates)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--commit",
        action="store_true",
        help="Write changes to the DB. Default is dry-run.",
    )
    args = p.parse_args()
    _run(commit=args.commit)


if __name__ == "__main__":
    main()
