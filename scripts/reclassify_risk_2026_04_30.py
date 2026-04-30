#!/usr/bin/env python3
"""One-shot re-classify of every Risk-family lead under the 2026-04-30
sub-specialism rules.

Why
---
The taxonomy update on 2026-04-30 added new phrase rules that affect
sub-specialism routing inside the Risk category:

  - 'risk models' / 'risk modelling' / 'risk modeling'  → Quant Risk
  - 'risk & control' / 'risk and control'               → Operational Risk
  - 'risk assurance'   (moved from Risk Management)     → Operational Risk
  - 'cross asset derivatives' / hyphenated form         → Market Risk
  - 'fire risk' / 'flood risk'                          → blocked entirely

The standard `prospero pipeline classify` step is gated on
``NOT EXISTS classification_results`` so it leaves already-classified
rows untouched. To make the new rules apply to existing leads we have
to delete + re-classify the affected rows.

Scope: every EnrichedJob whose current ClassificationResult has
``primary_taxonomy_key == 'risk'``. The new keywords sit inside the
risk taxonomy block, so anything currently outside Risk is unaffected
by this update.

Cascade: deletes the matching ScoreResult rows too, since the
composite score depends on the classification confidence and would
otherwise drift from the new classification_results row.

Usage
-----
  python3 scripts/reclassify_risk_2026_04_30.py             # dry run
  python3 scripts/reclassify_risk_2026_04_30.py --commit    # actually do it
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Allow `python scripts/...py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from vacancysoft.db.models import (
    ClassificationResult,
    EnrichedJob,
    ScoreResult,
)
from vacancysoft.db.session import SessionLocal
from vacancysoft.pipelines.classification_persistence import (
    classify_enriched_jobs,
    persist_classification_for_enriched_job,
)
from vacancysoft.pipelines.scoring_persistence import (
    persist_score_for_enriched_job,
    score_enriched_jobs,
)


def _affected_enriched_job_ids(session: Session) -> list[str]:
    return list(
        session.execute(
            select(ClassificationResult.enriched_job_id)
            .where(ClassificationResult.primary_taxonomy_key == "risk")
        ).scalars()
    )


def _summarise_current(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            ClassificationResult.sub_specialism,
            ClassificationResult.id,
        ).where(ClassificationResult.primary_taxonomy_key == "risk")
    ).all()
    counter: Counter[str] = Counter()
    for sub, _ in rows:
        counter[sub or "(none)"] += 1
    return dict(counter)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply changes. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        ids = _affected_enriched_job_ids(session)
        before = _summarise_current(session)
        print(f"Found {len(ids)} Risk-classified enriched_jobs.")
        print("Current sub-specialism distribution:")
        for sub, n in sorted(before.items(), key=lambda kv: -kv[1]):
            print(f"  {sub:<30} {n:>5}")

        if not args.commit:
            print("\n[DRY RUN] Pass --commit to delete & re-classify.")
            return 0

        if not ids:
            print("Nothing to do.")
            return 0

        # Delete classification + score rows for affected enriched_jobs.
        # SQLAlchemy's bulk delete with IN(:ids) chokes on >>1k rows on
        # some drivers — chunk the IDs.
        chunk_size = 500
        deleted_class = 0
        deleted_score = 0
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            res_c = session.execute(
                delete(ClassificationResult).where(
                    ClassificationResult.enriched_job_id.in_(chunk)
                )
            )
            res_s = session.execute(
                delete(ScoreResult).where(
                    ScoreResult.enriched_job_id.in_(chunk)
                )
            )
            deleted_class += res_c.rowcount or 0
            deleted_score += res_s.rowcount or 0
        session.commit()
        print(
            f"\nDeleted {deleted_class} classification_results "
            f"and {deleted_score} score_results."
        )

        # Re-classify each affected EnrichedJob explicitly. This bypasses
        # the standard classify_enriched_jobs() NOT-EXISTS gate but the
        # gate would now match (we just deleted) so we could call it
        # instead. Doing it explicitly keeps the scope tight to the IDs
        # we collected up front, avoiding accidental work if a parallel
        # process inserted other un-classified rows in the meantime.
        reclassified = 0
        for ejob_id in ids:
            ejob = session.get(EnrichedJob, ejob_id)
            if ejob is None:
                continue
            persist_classification_for_enriched_job(session, ejob)
            reclassified += 1
        session.commit()
        print(f"Re-classified {reclassified} enriched_jobs.")

        # Re-score: same scope. score_enriched_jobs is gated on
        # NOT EXISTS score_results, which now matches every affected
        # row, so we can use it directly.
        scored = score_enriched_jobs(session)
        print(f"Re-scored {scored} enriched_jobs.")

        after = _summarise_current(session)
        print("\nNew sub-specialism distribution (Risk only):")
        for sub, n in sorted(after.items(), key=lambda kv: -kv[1]):
            delta = n - before.get(sub, 0)
            sign = "+" if delta >= 0 else ""
            print(f"  {sub:<30} {n:>5}  ({sign}{delta})")

        # Report rows that fell out of Risk entirely (e.g. fire/flood
        # risk titles now blocked, or rows that didn't match any rule
        # under the new ruleset).
        dropped = len(ids) - sum(after.values())
        if dropped:
            print(f"\n{dropped} rows dropped out of Risk (likely blocked or unclassified).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
