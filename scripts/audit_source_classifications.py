#!/usr/bin/env python3
"""Full-DB source classification audit with incremental persistence + resume.

### Why this replaces `--all-active` on redetect_failing_sources.py

The 2026-04-24 run of ``redetect_failing_sources.py --all-active`` hung
at 1,180/1,347 (one source held a dead socket open with no per-probe
timeout) and lost 2h 21m of work because the old script only wrote its
findings at the final summary. This script fixes the three defects:

  1. **Per-source timeout** — each probe is wrapped in asyncio.wait_for,
     so a hung request can't stall the whole run.
  2. **Incremental JSONL persistence** — every probed source is appended
     to an output file as soon as its verdict is known. A crash loses
     only the in-flight sources, not the whole run.
  3. **Resume capability** — on startup the script reads the JSONL and
     skips any source_id already processed, so killing + restarting
     just picks up where it left off.

Plus: URL pattern detection is extended to recognise Avature and njoyn
(the upstream detector in ``api/source_detector.py`` doesn't know about
either) so the audit classifies the 8 Avature tenants correctly in one
pass.

### Phases

For each source:

  * **Phase 1 — URL pattern** (instant, zero network). Runs the upstream
    ``detect_platform()`` + the extended patterns below. If this returns
    anything other than ``generic_site``, that's the verdict — no probe.
  * **Phase 2 — HTTP probe** (only for generic_site verdicts). Fetches
    the page with httpx (short timeout, browser headers) and greps for
    embed hints (Workday iframe, Greenhouse embed script, etc.). If one
    is found, upgrade the verdict.
  * **Phase 3 — reachability mark** (independent of classification).
    Records HTTP status + latency so we can separately spot dead boards.

All three phases run under a 30s wall-clock timeout per source.

### Output

JSONL at ``./artifacts/source_audit.jsonl`` by default. One line per
source with fields:

    {
      "source_id": 489,
      "source_key": "generic_site_metro_bank_xxxxx",
      "employer": "Metro Bank",
      "current_adapter": "generic_site",
      "base_url": "https://metrobank.avature.net/amazingcareers",
      "detected_adapter": "avature",
      "detection_signal": "hostname-pattern",  # or "html-embed", "upstream", "error"
      "transition": true,                      # current_adapter != detected_adapter
      "reachable": true,                       # Phase 3 — can the server be reached?
      "probe_status": 200,                     # HTTP code, None if not probed
      "probe_method": "phase1_url_pattern",    # or "phase2_html_embed", "pattern_plus_probe"
      "error": null,                           # or "timeout" / "dns_fail" / "ssl" etc.
      "probed_at": "2026-04-24T16:35:02Z"
    }

### Subcommands

    # Probe + write JSONL (resumable)
    python3 scripts/audit_source_classifications.py audit

    # Summarise a completed JSONL
    python3 scripts/audit_source_classifications.py summary

    # Emit correction-script entries from the JSONL
    python3 scripts/audit_source_classifications.py gen-corrections

### Safety

  * Zero DB writes. This script is purely a diagnostic / read-only probe.
  * Apply findings via the existing ``scripts/apply_source_corrections.py``
    (manual review of each before commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from vacancysoft.api.source_detector import detect_platform  # noqa: E402
from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "source_audit.jsonl"

# Map upstream detector names to our internal adapter_name values.
# Detector returns "oracle_cloud" but our adapter is registered as "oracle"
# (see src/vacancysoft/adapters/oracle_cloud.py — class adapter_name="oracle").
# Mirrors the ADAPTER_MAP in scripts/redetect_failing_sources.py.
_DETECTOR_TO_ADAPTER = {
    "oracle_cloud": "oracle",
    "generic_site": "generic_site",  # pass-through
    "adp_workforcenow": "adp",
    "greenhouse_embed": "greenhouse",
}

# Aggregator adapters — these must NEVER be downgraded to generic_site by
# the audit, even if the probe finds no ATS fingerprint. An aggregator
# "source" IS the aggregator API endpoint; it's not a company career page
# that embeds an ATS. Mirrors src/vacancysoft/api/ledger.py:36.
_AGGREGATOR_ADAPTERS = frozenset({
    "adzuna", "reed", "efinancialcareers", "google_jobs", "coresignal",
})

# ── Extended URL patterns the upstream detector doesn't know ───────────────
# Each entry: (compiled regex, adapter, signal tag)
_EXTENDED_HOSTNAME_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.avature\.net", re.I), "avature", "hostname-avature"),
    (re.compile(r"recruitment\.macquarie\.com", re.I), "avature", "hostname-macquarie-avature"),
    (re.compile(r"\.njoyn\.com", re.I), "njoyn", "hostname-njoyn"),
]

# HTML-embed fingerprints (Phase 2). Keyed by search regex, value is the
# inferred adapter name.
_EMBED_FINGERPRINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.wd\d+\.myworkdayjobs\.com", re.I), "workday", "html-workday-iframe"),
    (re.compile(r"boards\.greenhouse\.io", re.I), "greenhouse", "html-greenhouse-embed"),
    (re.compile(r"jobs\.lever\.co", re.I), "lever", "html-lever-link"),
    (re.compile(r"\.icims\.com", re.I), "icims", "html-icims"),
    (re.compile(r"careers\.smartrecruiters\.com", re.I), "smartrecruiters", "html-smartrecruiters"),
    (re.compile(r"jobs\.ashbyhq\.com", re.I), "ashby", "html-ashby"),
    (re.compile(r"apply\.workable\.com", re.I), "workable", "html-workable"),
    (re.compile(r"\.avature\.net", re.I), "avature", "html-avature"),
    (re.compile(r"\.njoyn\.com", re.I), "njoyn", "html-njoyn"),
    (re.compile(r"\.oraclecloud\.com", re.I), "oracle_cloud", "html-oracle"),
    (re.compile(r"\.successfactors\.", re.I), "successfactors", "html-successfactors"),
    (re.compile(r"\.eightfold\.ai", re.I), "eightfold", "html-eightfold"),
    (re.compile(r"\.teamtailor\.com", re.I), "teamtailor", "html-teamtailor"),
    (re.compile(r"\.taleo\.net", re.I), "taleo", "html-taleo"),
    (re.compile(r"\.pinpointhq\.com", re.I), "pinpoint", "html-pinpoint"),
    (re.compile(r"\.hibob\.com", re.I), "hibob", "html-hibob"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
}


def _extended_hostname_detect(url: str) -> tuple[str, str] | None:
    """Return (adapter, signal) if URL hostname matches an extended pattern."""
    host_path = urlparse(url).netloc + urlparse(url).path
    for pattern, adapter, signal in _EXTENDED_HOSTNAME_PATTERNS:
        if pattern.search(host_path):
            return adapter, signal
    return None


def _phase1_url_pattern(url: str) -> dict[str, Any]:
    """URL-only detection. Prefers the upstream detector; falls back to extended patterns."""
    upstream = detect_platform(url)
    adapter = upstream["adapter"] if upstream else "generic_site"
    signal = "upstream-pattern"
    if adapter == "generic_site":
        ext = _extended_hostname_detect(url)
        if ext:
            adapter, signal = ext
    # Normalise to our internal adapter_name (detector uses "oracle_cloud"
    # but we register "oracle"; similar shim for adp variants).
    adapter = _DETECTOR_TO_ADAPTER.get(adapter, adapter)
    return {"adapter": adapter, "signal": signal}


def _phase2_html_embed(html: str) -> tuple[str, str] | None:
    """Return (adapter, signal) if HTML contains a recognisable ATS fingerprint."""
    # Only look at the first 200KB — enough to catch iframe srcs + embed scripts
    sample = html[:200_000] if len(html) > 200_000 else html
    for pattern, adapter, signal in _EMBED_FINGERPRINTS:
        if pattern.search(sample):
            return adapter, signal
    return None


async def _probe_url(url: str, timeout: float) -> dict[str, Any]:
    """Single httpx GET with browser headers. Never raises."""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "status": resp.status_code,
                "latency_ms": latency_ms,
                "body": resp.text if resp.status_code < 400 else "",
                "error": None,
                "final_url": str(resp.url),
            }
    except httpx.TimeoutException:
        return {"status": None, "latency_ms": int((time.perf_counter() - t0) * 1000), "body": "", "error": "timeout", "final_url": url}
    except httpx.ConnectError as exc:
        return {"status": None, "latency_ms": None, "body": "", "error": f"connect: {str(exc)[:100]}", "final_url": url}
    except Exception as exc:  # catch-all, script must not die mid-source
        return {"status": None, "latency_ms": None, "body": "", "error": f"{type(exc).__name__}: {str(exc)[:100]}", "final_url": url}


async def _audit_one(src_row: dict[str, Any], *, probe_timeout: float) -> dict[str, Any]:
    """Return the full audit dict for one Source row."""
    url = (src_row["base_url"] or "").strip()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base = {
        "source_id": src_row["id"],
        "source_key": src_row["source_key"],
        "employer": src_row["employer_name"],
        "current_adapter": src_row["adapter_name"],
        "base_url": url,
        "probed_at": now,
    }
    if not url:
        return base | {
            "detected_adapter": None, "detection_signal": "no-url",
            "transition": False, "reachable": False,
            "probe_status": None, "probe_method": "phase1_url_pattern", "error": "no url",
        }

    # Phase 1 — URL pattern
    phase1 = _phase1_url_pattern(url)

    # Phase 2 — probe for HTML embed hints ONLY if phase1 landed on generic_site
    detected = phase1["adapter"]
    signal = phase1["signal"]
    probe_status: int | None = None
    probe_error: str | None = None
    probe_method = "phase1_url_pattern"
    reachable = True  # optimistic; updated if we actually probe

    if phase1["adapter"] == "generic_site":
        probe = await _probe_url(url, probe_timeout)
        probe_status = probe["status"]
        probe_error = probe["error"]
        reachable = probe["status"] is not None and probe["status"] < 500
        if probe["body"]:
            embed = _phase2_html_embed(probe["body"])
            if embed:
                detected_raw, signal = embed
                detected = _DETECTOR_TO_ADAPTER.get(detected_raw, detected_raw)
                probe_method = "phase2_html_embed"
            else:
                probe_method = "pattern_plus_probe"
        else:
            probe_method = "pattern_plus_probe"

    # Aggregator protection — refuse to downgrade an aggregator source to
    # generic_site just because its server URL doesn't look like a career
    # page. Aggregator source rows point at API endpoints, not company sites.
    if (
        src_row["adapter_name"] in _AGGREGATOR_ADAPTERS
        and detected == "generic_site"
    ):
        detected = src_row["adapter_name"]
        signal = "aggregator-protected"
        probe_method = "aggregator_protected"

    # ATS-already-classified protection — if a source is currently on a known
    # ATS adapter and the detector only says "generic_site", DO NOT downgrade.
    # Common case: the Source.base_url is the company's careers landing page
    # (e.g. careers.wellsfargo.com) but the config_blob has the real ATS
    # endpoint (e.g. *.wd5.myworkdayjobs.com/wday/cxs/...). The adapter works
    # fine; we just can't confirm the ATS from base_url alone in Phase 1,
    # and Phase 2 didn't find an embed hint either. Keeping the current
    # classification is safer than blindly downgrading.
    if (
        src_row["adapter_name"] not in {"generic_site", None}
        and detected == "generic_site"
        and src_row["adapter_name"] not in _AGGREGATOR_ADAPTERS  # handled above
    ):
        detected = src_row["adapter_name"]
        signal = "current-adapter-protected"
        probe_method = "adapter_protected"

    transition = bool(detected) and detected != src_row["adapter_name"]
    return base | {
        "detected_adapter": detected,
        "detection_signal": signal,
        "transition": transition,
        "reachable": reachable,
        "probe_status": probe_status,
        "probe_method": probe_method,
        "error": probe_error,
    }


def _load_existing_source_ids(output_path: Path) -> set[int]:
    """Read the JSONL and return source_ids already processed (for resume)."""
    if not output_path.exists():
        return set()
    ids: set[int] = set()
    with output_path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
                ids.add(int(row["source_id"]))
            except Exception:
                continue
    return ids


def _load_candidate_rows(only_active: bool, limit: int | None) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        stmt = select(
            Source.id, Source.source_key, Source.employer_name,
            Source.adapter_name, Source.base_url, Source.active,
        )
        if only_active:
            stmt = stmt.where(Source.active.is_(True))
        stmt = stmt.order_by(Source.id)
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()
    return [
        {
            "id": r.id, "source_key": r.source_key, "employer_name": r.employer_name,
            "adapter_name": r.adapter_name, "base_url": r.base_url, "active": r.active,
        }
        for r in rows
    ]


async def _audit_with_timeout(src_row, probe_timeout: float, wall_timeout: float) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(_audit_one(src_row, probe_timeout=probe_timeout), timeout=wall_timeout)
    except asyncio.TimeoutError:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "source_id": src_row["id"], "source_key": src_row["source_key"],
            "employer": src_row["employer_name"],
            "current_adapter": src_row["adapter_name"],
            "base_url": src_row["base_url"],
            "detected_adapter": None, "detection_signal": "wall-timeout",
            "transition": False, "reachable": False,
            "probe_status": None, "probe_method": "timeout", "error": "wall-timeout",
            "probed_at": now,
        }


async def run_audit(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    only_active = not args.include_inactive
    candidates = _load_candidate_rows(only_active=only_active, limit=args.limit)
    already = _load_existing_source_ids(output_path) if args.resume else set()
    to_do = [r for r in candidates if r["id"] not in already]

    print(
        f"Source audit — {len(candidates)} candidates "
        f"({'active only' if only_active else 'all'}), "
        f"{len(already)} already in {output_path.name}, "
        f"{len(to_do)} to process"
    )
    if not to_do:
        print("  Nothing to do. Use `summary` to inspect findings.")
        return 0

    sem = asyncio.Semaphore(args.concurrency)
    probed_counter = [0]  # mutable ref for the worker closure
    t_start = time.time()
    lock = asyncio.Lock()

    async def _worker(src_row: dict[str, Any]) -> None:
        async with sem:
            result = await _audit_with_timeout(
                src_row,
                probe_timeout=args.probe_timeout,
                wall_timeout=args.wall_timeout,
            )
        async with lock:
            with output_path.open("a") as f:
                f.write(json.dumps(result) + "\n")
            probed_counter[0] += 1
            if probed_counter[0] % 25 == 0 or probed_counter[0] == len(to_do):
                rate = probed_counter[0] / max(time.time() - t_start, 0.001) * 60
                remaining = len(to_do) - probed_counter[0]
                eta_min = remaining / max(rate, 0.01)
                print(
                    f"  …probed {probed_counter[0]}/{len(to_do)} "
                    f"({rate:.1f}/min, ETA {eta_min:.1f} min)"
                )

    await asyncio.gather(*(_worker(r) for r in to_do))
    print(f"\nDone. Wrote {len(to_do)} new rows to {output_path}")
    print("Next: `python3 scripts/audit_source_classifications.py summary`")
    return 0


# ── Summary subcommand ──────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def run_summary(args: argparse.Namespace) -> int:
    path = Path(args.output)
    rows = _read_jsonl(path)
    if not rows:
        print(f"No rows in {path}")
        return 1
    print(f"Audit summary — {len(rows)} source rows from {path}")

    transitions = [r for r in rows if r.get("transition")]
    unreachable = [r for r in rows if not r.get("reachable")]
    unchanged = [r for r in rows if not r.get("transition") and r.get("reachable")]

    print(f"\n  transitions (reclassify candidates): {len(transitions)}")
    print(f"  unreachable / errored:                {len(unreachable)}")
    print(f"  unchanged (adapter already correct):  {len(unchanged)}")

    by_pair: Counter = Counter()
    for r in transitions:
        by_pair[(r.get("current_adapter"), r.get("detected_adapter"))] += 1

    if by_pair:
        print("\n=== Top transition pairs (current → detected) ===")
        for (cur, det), n in by_pair.most_common(15):
            print(f"  {cur:<20} → {det:<20}  {n}")

    by_signal: Counter = Counter(r.get("detection_signal") for r in transitions)
    if by_signal:
        print("\n=== Transitions by detection signal ===")
        for sig, n in by_signal.most_common():
            print(f"  {sig:<26}  {n}")

    if unreachable:
        print(f"\n=== First {min(10, len(unreachable))} unreachable sources ===")
        for r in unreachable[:10]:
            print(f"  src#{r['source_id']:<5}  {r['employer']:<32}  {r['base_url']:<60}  err={r.get('error')}")

    if transitions:
        print(f"\n=== First {min(10, len(transitions))} transitions ===")
        for r in transitions[:10]:
            print(
                f"  src#{r['source_id']:<5}  {r['employer']:<32}  "
                f"{r['current_adapter']:<14} → {r['detected_adapter']:<14}  ({r['detection_signal']})"
            )
    return 0


# ── gen-corrections subcommand ──────────────────────────────────────────────

def run_gen_corrections(args: argparse.Namespace) -> int:
    path = Path(args.output)
    rows = _read_jsonl(path)
    transitions = [r for r in rows if r.get("transition") and r.get("detected_adapter")]
    if not transitions:
        print("No transitions — nothing to generate.")
        return 0

    by_employer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in transitions:
        by_employer[r["employer"]].append(r)

    print("# Paste into scripts/apply_source_corrections.py's _CORRECTIONS list")
    print("# Review each entry before committing — auto-generated from audit JSONL.\n")
    for employer, entries in sorted(by_employer.items()):
        # If multiple rows for the same employer disagree, skip — needs manual review
        detected_set = {e["detected_adapter"] for e in entries}
        if len(detected_set) != 1:
            print(f"# MANUAL REVIEW  {employer}  — rows disagree on detected adapter: {detected_set}")
            continue
        e = entries[0]
        detected = e["detected_adapter"]
        print("    {")
        print(f'        "employer": {employer!r},')
        print(f'        "action": "reclassify",')
        print(f'        "adapter_name": {detected!r},')
        print(f'        "ats_family": {detected!r},')
        print(f'        "base_url": {e["base_url"]!r},')
        host = urlparse(e["base_url"]).netloc
        print(f'        "hostname": {host!r},')
        print(f'        "config_blob": {{')
        print(f'            "job_board_url": {e["base_url"]!r},')
        print(f'        }},')
        print(f'        "reason": "Source audit 2026-04-24: {e["current_adapter"]} → {detected} via {e["detection_signal"]}.",')
        print("    },")
    print(f"\n# Total: {len(transitions)} transitions across {len(by_employer)} employer(s)")
    return 0


# ── CLI wire-up ─────────────────────────────────────────────────────────────

def main() -> int:
    root = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    subparsers = root.add_subparsers(dest="cmd", required=True)

    p_audit = subparsers.add_parser("audit", help="Probe sources, write JSONL")
    p_audit.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL output path.")
    p_audit.add_argument("--limit", type=int, help="Max sources to process (for testing).")
    p_audit.add_argument("--include-inactive", action="store_true", help="Include inactive sources too.")
    p_audit.add_argument("--concurrency", type=int, default=10, help="Concurrent probes (default 10).")
    p_audit.add_argument("--probe-timeout", type=float, default=10.0, help="httpx GET timeout seconds.")
    p_audit.add_argument("--wall-timeout", type=float, default=30.0, help="Per-source overall timeout.")
    p_audit.add_argument("--no-resume", dest="resume", action="store_false", help="Don't skip already-probed rows.")
    p_audit.set_defaults(resume=True)

    p_summary = subparsers.add_parser("summary", help="Print summary of a JSONL audit file")
    p_summary.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL path to summarise.")

    p_gen = subparsers.add_parser("gen-corrections", help="Emit correction entries from JSONL")
    p_gen.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL path.")

    args = root.parse_args()
    if args.cmd == "audit":
        return asyncio.run(run_audit(args))
    if args.cmd == "summary":
        return run_summary(args)
    if args.cmd == "gen-corrections":
        return run_gen_corrections(args)
    root.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
