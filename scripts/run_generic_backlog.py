"""Scrape the backlog of `generic_site` sources that have never run.

Designed to be kicked off with nohup so it survives VS Code restarts:

    nohup python3 scripts/run_generic_backlog.py > /tmp/prospero-generic-run.log 2>&1 &

Behaviour:
  * Queries active generic_site sources with zero SourceRun rows (`never_ran`).
  * Calls `vacancysoft.worker.tasks.scrape_source` for each source — same code path
    the API `/api/sources/{id}/scrape` endpoint + the ARQ worker use.
  * Logs a single line per source to stdout so `tail -f` shows progress.
  * Runs sequentially — one Playwright browser at a time for safety.
  * Does NOT raise on individual source failures; records them in a summary.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

# Make the package importable when run as a script
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from vacancysoft.db.session import SessionLocal
from vacancysoft.worker.tasks import scrape_source


def _print(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def main() -> None:
    # Pick up optional knobs from env so you can tune without code changes
    adapter = os.getenv("RUN_ADAPTER", "generic_site")
    only_never_run = os.getenv("ONLY_NEVER_RUN", "1") == "1"
    max_count_env = os.getenv("RUN_LIMIT")
    per_source_timeout = int(os.getenv("PER_SOURCE_TIMEOUT_S", "300"))

    # Query target source IDs + names
    with SessionLocal() as s:
        if only_never_run:
            rows = s.execute(text("""
                SELECT src.id, src.employer_name, src.base_url
                FROM sources src
                WHERE src.active = true
                  AND src.adapter_name = :a
                  AND NOT EXISTS (SELECT 1 FROM source_runs sr WHERE sr.source_id = src.id)
                ORDER BY src.id
            """), {"a": adapter}).all()
        else:
            rows = s.execute(text("""
                SELECT src.id, src.employer_name, src.base_url
                FROM sources src
                WHERE src.active = true
                  AND src.adapter_name = :a
                ORDER BY src.id
            """), {"a": adapter}).all()

    max_count = int(max_count_env) if max_count_env else len(rows)
    rows = rows[:max_count]
    total = len(rows)

    _print(f"Runner starting — adapter={adapter} only_never_run={only_never_run} targets={total} per_source_timeout={per_source_timeout}s")
    if total == 0:
        _print("Nothing to do — all sources have already run at least once.")
        return

    summary = {"ok": 0, "empty": 0, "timeout": 0, "error": 0, "raw_jobs_total": 0}
    start_wall = time.monotonic()

    for idx, (sid, name, url) in enumerate(rows, 1):
        t0 = time.monotonic()
        label = f"[{idx}/{total}] id={sid} {name!r:40} -> {url[:60]}"
        try:
            await asyncio.wait_for(scrape_source({}, sid), timeout=per_source_timeout + 30)
            # Look up how many raw jobs this source now has
            with SessionLocal() as s:
                raw = s.execute(
                    text("SELECT COUNT(*) FROM raw_jobs WHERE source_id = :id"),
                    {"id": sid},
                ).scalar() or 0
                last_status = s.execute(
                    text("SELECT status FROM source_runs WHERE source_id=:id ORDER BY created_at DESC LIMIT 1"),
                    {"id": sid},
                ).scalar()
            dt = time.monotonic() - t0
            if raw == 0:
                summary["empty"] += 1
                _print(f"{label}  EMPTY     raw=0  status={last_status}  {dt:.0f}s")
            else:
                summary["ok"] += 1
                summary["raw_jobs_total"] += raw
                _print(f"{label}  OK        raw={raw}  status={last_status}  {dt:.0f}s")
        except asyncio.TimeoutError:
            summary["timeout"] += 1
            _print(f"{label}  TIMEOUT   after {per_source_timeout+30}s")
        except Exception as exc:  # noqa: BLE001
            summary["error"] += 1
            _print(f"{label}  ERROR     {type(exc).__name__}: {exc!s:.120}")

        # Summary every 10 sources
        if idx % 10 == 0 or idx == total:
            elapsed = time.monotonic() - start_wall
            rate = elapsed / idx
            remaining = (total - idx) * rate
            _print(
                f"  Progress {idx}/{total}  ok={summary['ok']} empty={summary['empty']} "
                f"timeout={summary['timeout']} error={summary['error']}  raw_jobs={summary['raw_jobs_total']}  "
                f"elapsed={elapsed/60:.1f}m  eta={remaining/60:.1f}m"
            )

    total_elapsed = time.monotonic() - start_wall
    _print(
        f"DONE in {total_elapsed/60:.1f} min. "
        f"ok={summary['ok']} empty={summary['empty']} timeout={summary['timeout']} error={summary['error']}  "
        f"raw_jobs={summary['raw_jobs_total']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
