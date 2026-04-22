#!/usr/bin/env python3
"""Mark sources `active=False` when their URL is genuinely dead.

Phase 6 of the 2026-04-22 adapter-failures close-out. After Phase 1's
re-detection sweep reclassifies the misclassified sources, a small
long-tail of genuinely-dead or anti-scraping boards remains:

  * Pinpoint: 2 HTTP 404s (`chetwoodbank.co.uk`, `scruttonbland.co.uk`)
  * Pinpoint: 1 HTTP 403 (`sumer.co.uk`)
  * Silkroad: 1 DNS-dead subdomain (`opco-openhire.silkroad.com`)
  * iCIMS: 1 timeout (`quantbottech`) + 1 nav-fail (`therightmortgage.co.uk`)

These aren't re-routable — the URLs are actually broken. We mark them
`active=False` so the scheduler stops wasting time on them and the
audits stop flagging them. Each deactivation records a note to
`Source.notes` so the next operator knows why.

### Safety

  * Default is DRY-RUN. Must pass --commit to write.
  * Each candidate is RE-PROBED with a longer timeout before marking
    dead — a site that was down yesterday might be up today.
  * `--unmark` reverses: given a source_key, flip active back to
    True and strip the "dead_board_sweep" note. Useful if the script
    mis-marks a board that later comes back online.

USAGE

    # Preview — no writes
    python3 scripts/mark_dead_boards.py --dry-run

    # Scope to one adapter
    python3 scripts/mark_dead_boards.py --dry-run --adapter pinpoint

    # Commit
    python3 scripts/mark_dead_boards.py --commit

    # Reverse (source came back online)
    python3 scripts/mark_dead_boards.py --unmark pinpoint_scruttonbland_xxxxxxxx

ROLLBACK

    # DB backup first (pg_dump), then:
    # UPDATE sources SET active=true WHERE id IN (...) — or use --unmark.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source, SourceRun  # noqa: E402


# Error patterns that indicate a genuinely-dead board (as opposed to
# a misclassification or transient Cloudflare challenge). Matched
# against the last SourceRun's diagnostics_blob.error_message.
DEAD_ERROR_PATTERNS = (
    "[Errno 8] nodename nor servname provided, or not known",      # DNS fail
    "Client error '404 Not Found'",                                 # gone from the site
    "Page.goto: Navigation to ",                                    # Playwright nav failure
)
# HTTP 403 is NOT in this list — it might be anti-scraping (fixable
# via Firefox fallback / UA rotation). Flag for manual review instead.
AMBIGUOUS_ERROR_PATTERNS = (
    "Client error '403 Forbidden'",
    "Page.goto: Timeout",
)


async def _probe_url(url: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Re-probe a URL. Returns (reachable, short_reason).

    "Reachable" here is deliberately wider than "2xx OK" — any response
    from the server counts (including 403 / 401 / 429), because those
    indicate the DNS resolves and a server is answering. Those sites
    might need anti-scraping workarounds (Firefox fallback, UA
    rotation) but they're NOT dead. Only DNS failures, connection
    resets, timeouts, and 404 / 410 / 5xx server errors are treated as
    dead — the rest keep `active=True` and get flagged for manual
    review instead.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
            })
            # 2xx / 3xx / anti-scrape rejections (401/403/429) → site is up
            if resp.status_code in (401, 403, 429):
                return True, f"HTTP {resp.status_code} (anti-scrape, not dead)"
            if 200 <= resp.status_code < 400:
                return True, f"HTTP {resp.status_code}"
            # 404 / 410 / 5xx → genuinely dead or broken at source
            return False, f"HTTP {resp.status_code}"
    except httpx.ConnectError as exc:
        return False, f"ConnectError: {exc}"
    except httpx.TimeoutException:
        return False, "Timeout"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _find_candidates(session, adapter: str | None) -> list[tuple[Source, SourceRun, str]]:
    """Find sources whose most recent SourceRun matches a dead-board
    error pattern. Returns (source, last_run, matched_pattern)."""
    base = select(Source).where(Source.active.is_(True))
    if adapter:
        base = base.where(Source.adapter_name == adapter)

    out: list[tuple[Source, SourceRun, str]] = []
    sources = session.execute(base).scalars().all()
    for src in sources:
        last_run = session.execute(
            select(SourceRun)
            .where(SourceRun.source_id == src.id)
            .order_by(SourceRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_run is None or (last_run.status or "").lower() != "error":
            continue
        err_msg = (last_run.diagnostics_blob or {}).get("error_message", "")
        for pattern in DEAD_ERROR_PATTERNS:
            if pattern in err_msg:
                out.append((src, last_run, pattern))
                break
    return out


async def _run(args: argparse.Namespace) -> int:
    with SessionLocal() as s:
        # ── Unmark mode ────────────────────────────────────────────
        if args.unmark:
            src = s.execute(
                select(Source).where(Source.source_key == args.unmark)
            ).scalar_one_or_none()
            if src is None:
                print(f"No source with source_key={args.unmark!r}")
                return 1
            was_active = src.active
            src.active = True
            notes = src.notes or ""
            # Strip the sweep note line
            lines = [ln for ln in notes.split("\n") if "dead_board_sweep" not in ln]
            src.notes = "\n".join(lines).strip() or None
            if args.commit:
                s.commit()
                print(f"✔ Un-marked src#{src.id} ({src.source_key}). Active: {was_active} → True")
            else:
                print(f"[dry-run] Would un-mark src#{src.id} ({src.source_key}). Active: {was_active} → True")
            return 0

        # ── Standard sweep ─────────────────────────────────────────
        candidates = _find_candidates(s, args.adapter)
        print(f"Found {len(candidates)} candidate sources "
              f"(adapter={args.adapter or '*'} dry_run={not args.commit})")
        if not candidates:
            return 0

        to_deactivate: list[Source] = []
        still_reachable: list[Source] = []

        for i, (src, last_run, pattern) in enumerate(candidates, start=1):
            print(f"  [{i}/{len(candidates)}] probing src#{src.id} ({src.source_key})…",
                  flush=True, end="")
            reachable, reason = await _probe_url(src.base_url, timeout=args.timeout)
            print(f" {reason}")
            if reachable:
                still_reachable.append(src)
            else:
                to_deactivate.append(src)

        print()
        print("─" * 70)
        print(f"  still reachable (false alarm, kept active): {len(still_reachable)}")
        print(f"  confirmed dead (would deactivate):          {len(to_deactivate)}")

        if still_reachable:
            print()
            print("Still-reachable sources (kept active):")
            for src in still_reachable:
                print(f"  src#{src.id:>5} {src.source_key:<45} {src.base_url[:60]}")

        if to_deactivate:
            print()
            print("Dead sources:")
            for src in to_deactivate:
                print(f"  src#{src.id:>5} {src.source_key:<45} {src.base_url[:60]}")

        if args.commit and to_deactivate:
            note_ts = datetime.utcnow().strftime("%Y-%m-%d")
            for src in to_deactivate:
                src.active = False
                notes = src.notes or ""
                src.notes = (
                    f"{notes}\n{note_ts} dead_board_sweep: URL unreachable "
                    f"(re-probe confirmed). Use scripts/mark_dead_boards.py "
                    f"--unmark {src.source_key} to re-enable."
                ).strip()
            s.commit()
            print()
            print(f"✔ Deactivated {len(to_deactivate)} sources.")

        if not args.commit:
            print()
            print("(dry-run — pass --commit to actually update)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--commit", action="store_true", help="Actually write. Default: dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run flag (default when --commit absent).")
    parser.add_argument("--adapter", help="Restrict to one adapter (e.g. 'pinpoint').")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP probe timeout (default 20s).")
    parser.add_argument("--unmark", metavar="SOURCE_KEY", help="Reverse a previous deactivation by source_key.")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
