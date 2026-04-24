#!/usr/bin/env python3
"""Apply operator-discovered corrections to seeded sources.

Some employers in ``configs/config.py`` ship with the wrong ATS or a URL
that points at a corporate career landing page rather than the real ATS.
The seed loader can't tell these cases apart from genuine generic-site
entries, so the sources get ingested wrong and fail every scrape run
until an operator investigates.

This script holds the list of corrections we've investigated (via live
probe / redetect script) and re-applies them to the DB on demand. It
matches by ``employer_name`` (case-insensitive) rather than ``id`` or
``source_key`` so the fix survives a DB reseed — fresh seed rows get
re-corrected.

### Provenance

All corrections here MUST have a Reason line noting:
  - how the correction was verified (live probe, API endpoint, etc.)
  - when it was discovered (so we can sanity-check years from now)

### Safety

  * Default is DRY-RUN — must pass ``--commit`` to write.
  * Idempotent: if the row already matches the corrected shape, the
    script is a no-op for that row (won't touch ``updated_at``).
  * Match is case-insensitive on ``employer_name`` only; duplicate
    rows for the same employer will ALL be corrected.

### Usage

    # Preview
    python3 scripts/apply_source_corrections.py

    # Commit
    python3 scripts/apply_source_corrections.py --commit

### Rollback

This script intentionally has no un-apply path — the corrections here
represent "truth" (the real ATS, verified live). Rolling back just
means reverting to known-broken state. If a correction here is wrong,
delete it from ``_CORRECTIONS`` rather than adding an unapply hook.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import func, select  # noqa: E402

from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source  # noqa: E402


# ── Corrections ─────────────────────────────────────────────────────────────
#
# Each entry has:
#   employer:       case-insensitive match against Source.employer_name
#   action:         "reclassify" or "deactivate"
#   reason:         one-liner, shown in Source.notes and the script summary
#
# Reclassify adds: adapter_name, ats_family, base_url, hostname, config_blob
# Deactivate adds: (nothing — just flips active=False)
#
# Append new entries here as you discover misclassifications.
# -----------------------------------------------------------------------------
_CORRECTIONS: list[dict] = [
    {
        "employer": "The Hartford",
        "action": "reclassify",
        "adapter_name": "workday",
        "ats_family": "workday",
        "base_url": "https://thehartford.wd5.myworkdayjobs.com/en-US/Careers_External",
        "hostname": "thehartford.wd5.myworkdayjobs.com",
        "config_blob": {
            "endpoint_url": "https://thehartford.wd5.myworkdayjobs.com/wday/cxs/thehartford/Careers_External/jobs",
            "job_board_url": "https://thehartford.wd5.myworkdayjobs.com/en-US/Careers_External",
            "tenant": "thehartford",
            "shard": "wd5",
            "site_path": "Careers_External",
        },
        "reason": (
            "Seeded with corporate-careers URL (thehartford.com/careers); "
            "real ATS is Workday at *.wd5.myworkdayjobs.com — verified 379 jobs "
            "on the live CXS endpoint (2026-04-24)."
        ),
    },
    {
        "employer": "Yieldstreet",
        "action": "deactivate",
        "reason": (
            "yieldstreet.com DNS unresolvable from scraper (2026-04-24). "
            "Reactivate and supply a valid ATS URL if DNS recovers or a real "
            "hosted ATS is discovered."
        ),
    },
]


def _config_matches(current: dict | None, desired: dict) -> bool:
    """Return True if the source's config already contains every desired key=value."""
    if not isinstance(current, dict):
        return False
    return all(current.get(k) == v for k, v in desired.items())


def _apply_reclassify(session, src: Source, rule: dict, commit: bool) -> str:
    """Return a one-line outcome description for this source."""
    desired_config = rule["config_blob"]
    already_correct = (
        src.adapter_name == rule["adapter_name"]
        and src.ats_family == rule["ats_family"]
        and src.base_url == rule["base_url"]
        and src.hostname == rule["hostname"]
        and _config_matches(src.config_blob, desired_config)
    )
    if already_correct:
        return f"  skip   id={src.id:<5} already correct"

    if not commit:
        return (
            f"  would  id={src.id:<5} "
            f"{src.adapter_name} → {rule['adapter_name']}, "
            f"base_url: {src.base_url} → {rule['base_url']}"
        )

    src.adapter_name = rule["adapter_name"]
    src.ats_family = rule["ats_family"]
    src.base_url = rule["base_url"]
    src.hostname = rule["hostname"]
    # Merge keeps any operator-supplied extras the seeder added
    merged = dict(src.config_blob or {})
    merged.update(desired_config)
    src.config_blob = merged
    src.notes = (src.notes or "") + f" [2026-04-24 correction: {rule['reason']}]"
    src.updated_at = datetime.now(timezone.utc)
    return f"  fix    id={src.id:<5} reclassified to {rule['adapter_name']}"


def _apply_deactivate(src: Source, rule: dict, commit: bool) -> str:
    if not src.active:
        return f"  skip   id={src.id:<5} already inactive"
    if not commit:
        return f"  would  id={src.id:<5} deactivate ({src.adapter_name})"

    src.active = False
    src.archived_at = datetime.now(timezone.utc)
    src.notes = (src.notes or "") + f" [2026-04-24 correction: {rule['reason']}]"
    src.updated_at = datetime.now(timezone.utc)
    return f"  fix    id={src.id:<5} deactivated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply seeded-source corrections.")
    parser.add_argument("--commit", action="store_true", help="Write changes. Default: dry-run.")
    args = parser.parse_args()

    print(
        f"Apply source corrections — {len(_CORRECTIONS)} rule(s) "
        f"[{'COMMIT' if args.commit else 'DRY-RUN'}]"
    )

    total_fixed = 0
    total_skipped = 0
    total_missing = 0

    with SessionLocal() as session:
        for rule in _CORRECTIONS:
            employer = rule["employer"]
            action = rule["action"]
            print(f"\n[{action}] {employer} — {rule['reason']}")

            matches = list(session.execute(
                select(Source).where(func.lower(Source.employer_name) == employer.lower())
            ).scalars())

            if not matches:
                print(f"  miss   no Source rows found for employer={employer!r}")
                total_missing += 1
                continue

            for src in matches:
                if action == "reclassify":
                    msg = _apply_reclassify(session, src, rule, args.commit)
                elif action == "deactivate":
                    msg = _apply_deactivate(src, rule, args.commit)
                else:
                    msg = f"  err    unknown action={action!r}"
                print(msg)
                if msg.startswith("  fix") or msg.startswith("  would"):
                    total_fixed += 1
                elif msg.startswith("  skip"):
                    total_skipped += 1

        if args.commit:
            session.commit()
            print(f"\nDone. fixed={total_fixed}, already-correct={total_skipped}, missing-employer={total_missing}")
        else:
            print(f"\nDry-run complete. would-fix={total_fixed}, already-correct={total_skipped}, missing-employer={total_missing}")
            print("(pass --commit to write)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
