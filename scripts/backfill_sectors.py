"""Re-classify every Source row via ``detect_sector`` and write the
result to ``sources.sector``.

Idempotent and safe to re-run. Use after editing
``configs/sector_taxonomy.yaml`` to propagate the new mapping.

Default mode is **dry-run** — passes ``--commit`` to write changes.

Usage:

    python3 scripts/backfill_sectors.py            # dry run, prints the plan
    python3 scripts/backfill_sectors.py --commit   # apply
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select, update

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source
from vacancysoft.source_registry.sector_classifier import (
    detect_sector,
    invalidate_cache,
)


def _run(commit: bool) -> None:
    invalidate_cache()  # always re-read the YAML
    by_sector_after: Counter[str] = Counter()
    changes: list[tuple[int, str, str, str]] = []  # (id, employer, old, new)

    with SessionLocal() as session:
        sources = list(session.execute(select(Source)).scalars())
        for s in sources:
            new_sector = detect_sector(
                s.employer_name or "",
                s.adapter_name or "",
                s.base_url or "",
            )
            by_sector_after[new_sector] += 1
            if (s.sector or "unknown") != new_sector:
                changes.append((s.id, s.employer_name, s.sector or "unknown", new_sector))

        print(f"Sources scanned: {len(sources)}")
        print(f"Sources to update: {len(changes)}")
        print()
        print("Final distribution by sector (after this run):")
        for sector, n in by_sector_after.most_common():
            print(f"  {sector:<24} {n}")
        print()

        if not changes:
            print("No changes — sectors already in sync with the taxonomy.")
            return

        # Show a sample of changes for sanity
        print("First 30 changes:")
        for sid, name, old, new in changes[:30]:
            print(f"  src#{sid:<5} {name[:38]:<38}  {old} → {new}")
        if len(changes) > 30:
            print(f"  ... ({len(changes) - 30} more)")
        print()

        if not commit:
            print("Dry run — pass --commit to apply.")
            return

        # Apply in a single transaction
        for sid, _, _, new in changes:
            session.execute(
                update(Source).where(Source.id == sid).values(sector=new)
            )
        session.commit()
        print(f"Committed {len(changes)} updates.")


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
