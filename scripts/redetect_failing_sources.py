#!/usr/bin/env python3
"""Re-detect the platform for every Source whose most recent scrape errored.

Context: the 2026-04-22 full-pipeline run surfaced 221 SourceRun errors.
Root-cause analysis showed ~70% of the errors are "source misclassified at
registration" — the `sources.adapter_name` column was set to the wrong
adapter, so the scheduler dispatches them to a scraper that can't handle
their URL. Lever has 103 such sources pointing at BambooHR / custom /
current-vacancies.com domains; iCIMS has 16 pointing at jobs.shell.com,
careers.pypl.com, etc.

This script:
  1. Finds every Source with `active=True` whose MOST RECENT SourceRun
     has status='error' (i.e. currently failing in the live pipeline).
  2. Calls `detect_and_validate(base_url)` from the existing
     source_detector module to determine the correct adapter.
  3. Compares the detected adapter to the current `adapter_name`.
  4. On dry-run: prints every (old -> new) transition + reachability.
  5. On --commit: updates `adapter_name`, `ats_family` (looked up via
     PLATFORM_REGISTRY — matches the canonical pair, NOT the raw
     detected name), and `config_blob.slug` where the new adapter
     needs a slug.

Unreachable URLs (DNS fail / HTTP 404) are logged as candidates for
Phase 6 (dead-board cleanup) and NOT reclassified here.

SAFETY

  * --dry-run is the default. Must pass --commit to write.
  * DB backup first:
      cp .data/prospero.db .data/backups/prospero-pre-redetect-$(date +%Y-%m-%d).db
  * `source_detector.detect_and_validate` does a live HTTP probe per
     source, so the script takes ~1s per source. 128-ish failing
     sources = ~2-3 min.
  * The reclassification is one-way. Run a scrape after committing
     to verify the new adapter works against each re-routed source.

USAGE

    # Preview (no writes)
    python3 scripts/redetect_failing_sources.py --dry-run

    # Scope to one adapter's failure cluster
    python3 scripts/redetect_failing_sources.py --dry-run --current-adapter lever

    # Commit — live scrape detection + DB update
    python3 scripts/redetect_failing_sources.py --commit

    # Force re-detection of ALL active sources regardless of last run
    # status (slower, lower value — not default; use if you suspect
    # misclassifications in the "currently-healthy" pool).
    python3 scripts/redetect_failing_sources.py --dry-run --all-active

VERIFICATION

    # After commit, re-run the affected adapters
    for A in lever icims hibob bamboohr silkroad successfactors pinpoint; do
        prospero pipeline run --adapter "$A"
    done
    # Then re-measure per-adapter failure rate

ROLLBACK

    # Restore DB from backup
    cp .data/backups/prospero-pre-redetect-<date>.db .data/prospero.db
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from vacancysoft.api.source_detector import detect_and_validate  # noqa: E402
from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source, SourceRun  # noqa: E402
from vacancysoft.source_registry.config_seed_loader import PLATFORM_REGISTRY  # noqa: E402

# Same mapping as src/vacancysoft/api/routes/sources.py:55 — kept in
# sync. If this grows, DRY it by extracting to a shared module.
ADAPTER_MAP: dict[str, str] = {
    "greenhouse": "greenhouse", "workday": "workday", "lever": "lever",
    "icims": "icims", "ashby": "ashby", "smartrecruiters": "smartrecruiters",
    "workable": "workable", "oracle_cloud": "oracle", "successfactors": "successfactors",
    "eightfold": "eightfold", "pinpoint": "pinpoint", "hibob": "hibob",
    "taleo": "taleo", "teamtailor": "teamtailor", "generic_site": "generic_browser",
}


def _find_latest_failing_sources(session: Session, current_adapter: str | None) -> list[tuple[Source, SourceRun]]:
    """Sources whose MOST RECENT SourceRun has status='error'.

    N+1 intentionally — the two-step query keeps the per-row logic
    readable at the cost of ~N queries. For ~150 sources it's a
    sub-second operation.
    """
    base = select(Source).where(Source.active.is_(True))
    if current_adapter:
        base = base.where(Source.adapter_name == current_adapter)
    active_sources = session.execute(base).scalars().all()

    out: list[tuple[Source, SourceRun]] = []
    for src in active_sources:
        last_run = session.execute(
            select(SourceRun)
            .where(SourceRun.source_id == src.id)
            .order_by(SourceRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_run is None:
            continue
        if (last_run.status or "").lower() == "error":
            out.append((src, last_run))
    return out


def _find_all_active(session: Session, current_adapter: str | None) -> list[tuple[Source, SourceRun | None]]:
    """Every active source — used with --all-active for a full sweep."""
    base = select(Source).where(Source.active.is_(True))
    if current_adapter:
        base = base.where(Source.adapter_name == current_adapter)
    return [(src, None) for src in session.execute(base).scalars().all()]


async def _probe(src: Source, timeout: float) -> dict[str, Any]:
    """Call detect_and_validate; return a uniform result dict."""
    try:
        return await detect_and_validate(src.base_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": None,
            "slug": None,
            "url": src.base_url,
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _run(args: argparse.Namespace) -> int:
    with SessionLocal() as s:
        if args.all_active:
            candidates = _find_all_active(s, args.current_adapter)
        else:
            candidates = _find_latest_failing_sources(s, args.current_adapter)

        total = len(candidates)
        print(
            f"Found {total} candidate sources "
            f"(current_adapter={args.current_adapter or '*'} "
            f"mode={'all-active' if args.all_active else 'latest-error'} "
            f"dry_run={not args.commit})"
        )
        if args.limit:
            candidates = candidates[:args.limit]
            print(f"  Limiting to first {args.limit}")
        if not candidates:
            return 0

        # Tally transitions + sample for printout
        transitions: Counter = Counter()
        unreachable: list[tuple[Source, str]] = []
        unchanged: int = 0
        no_change_high_conf: int = 0   # classification confirmed
        samples_per_transition: dict[tuple[str, str], list[Source]] = defaultdict(list)

        for i, (src, _last_run) in enumerate(candidates, start=1):
            if i % 10 == 0:
                print(f"  …probed {i}/{len(candidates)}", flush=True)
            result = await _probe(src, args.timeout)
            detected_adapter = result.get("adapter")
            if not result.get("reachable"):
                unreachable.append((src, result.get("error") or "unreachable"))
                continue
            if not detected_adapter or detected_adapter == src.adapter_name:
                unchanged += 1
                if detected_adapter == src.adapter_name:
                    no_change_high_conf += 1
                continue

            key = (src.adapter_name, detected_adapter)
            transitions[key] += 1
            if len(samples_per_transition[key]) < 5:
                samples_per_transition[key].append(src)

            if args.commit:
                # Look up the canonical pair — matches PLATFORM_REGISTRY
                # exactly like add_source() and the fixed diagnose endpoint
                platform_key = ADAPTER_MAP.get(detected_adapter, "generic_browser")
                meta = PLATFORM_REGISTRY.get(platform_key, PLATFORM_REGISTRY["generic_browser"])
                src.adapter_name = meta["adapter"]
                src.ats_family = meta["ats_family"]
                config = dict(src.config_blob or {})
                config["job_board_url"] = result.get("url") or src.base_url
                if result.get("slug"):
                    config["slug"] = result["slug"]
                src.config_blob = config

        if args.commit:
            s.commit()

        # ── Report ────────────────────────────────────────────────
        print()
        print("─" * 70)
        print(f"Probed {len(candidates)} sources. Summary:")
        print(f"  transitions (would-reclassify): {sum(transitions.values())}")
        print(f"  unchanged (already correct):    {unchanged} "
              f"({no_change_high_conf} with high-confidence re-confirm)")
        print(f"  unreachable (DNS/HTTP fail):    {len(unreachable)} "
              f"— candidates for Phase 6 dead-board cleanup")
        print()
        if transitions:
            print("Transitions (old → new):")
            for (old, new), cnt in sorted(transitions.items(), key=lambda t: -t[1]):
                print(f"  [{cnt:>3}x] {old:<20} → {new}")
                for src in samples_per_transition[(old, new)][:3]:
                    print(f"          - src#{src.id:>5} {src.source_key:<42} {src.base_url[:55]}")
        if unreachable:
            print()
            print(f"Unreachable sources (first 10):")
            for src, err in unreachable[:10]:
                print(f"  src#{src.id:>5} {src.source_key:<42} {err[:70]}")

        if args.commit:
            print()
            print("✔ Committed. Run a targeted scrape to verify:")
            print("  for A in lever icims hibob bamboohr silkroad successfactors pinpoint; do")
            print("      prospero pipeline run --adapter \"$A\"")
            print("  done")
        else:
            print()
            print("(dry-run — pass --commit to actually update)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--commit", action="store_true", help="Actually write. Default: dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Explicit dry-run flag (this is the default when --commit is absent; kept for clarity in docs).")
    parser.add_argument("--current-adapter", help="Restrict to sources with this current adapter_name (e.g. 'lever').")
    parser.add_argument("--all-active", action="store_true",
                        help="Probe ALL active sources, not just currently-failing ones. Slower.")
    parser.add_argument("--limit", type=int, help="Max sources to probe.")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="Per-probe HTTP timeout in seconds (default 15).")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
