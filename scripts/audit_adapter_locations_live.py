"""Live audit of adapter location extraction.

Actually runs selected adapters against sample boards from configs/config.py
and reports what percentage of returned records have a populated
``location_raw`` — the thing the static audit can't tell you.

By default, audits only the four adapters that were recently modified
(lever, selectminds, successfactors, generic_site) against a small number
of boards per adapter, to keep a full run bounded (minutes, not hours).

Usage
-----
    # Default: audit the 4 modified adapters, 2 boards each, 20-record cap.
    python3 scripts/audit_adapter_locations_live.py

    # Audit a specific adapter
    python3 scripts/audit_adapter_locations_live.py --only lever selectminds

    # More boards per adapter, larger sample
    python3 scripts/audit_adapter_locations_live.py --boards 3 --sample 50

    # Audit every adapter in ADAPTER_REGISTRY (slow)
    python3 scripts/audit_adapter_locations_live.py --all

    # Machine-readable output
    python3 scripts/audit_adapter_locations_live.py --json > /tmp/live_audit.json

Notes
-----
* Requires the repo's virtualenv / dependencies (playwright, httpx, etc.).
* generic_site is capped to ``max_pages=1`` and ``scroll_rounds=1`` for speed.
* Network failures are caught per-board; one failing board won't abort the run.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    _RICH = True
except ImportError:
    _RICH = False


# Map adapter_name → CONFIG_ATTR name in configs/config.py
ADAPTER_TO_CONFIG = {
    "lever": "LEVER_BOARDS",
    "selectminds": "SELECTMINDS_BOARDS",
    "successfactors": "SUCCESSFACTORS_BOARDS",
    "generic_site": "GENERIC_BROWSER_BOARDS",
    "greenhouse": "GREENHOUSE_BOARDS",
    "ashby": "ASHBY_BOARDS",
    "smartrecruiters": "SMARTRECRUITERS_BOARDS",
    "workable": "WORKABLE_BOARDS",
    "eightfold": "EIGHTFOLD_BOARDS",
    "workday": "WORKDAY_BOARDS",
    "oracle_cloud": "ORACLE_BOARDS",
    "icims": "ICIMS_BOARDS",
    "hibob": "HIBOB_BOARDS",
    "silkroad": "SILKROAD_BOARDS",
    "taleo": "TALEO_BOARDS",
    "pinpoint": "PINPOINT_BOARDS",
    "adzuna": "ADZUNA_BOARDS",
    "reed": "REED_BOARDS",
    "efinancialcareers": "EFINANCIALCAREERS_BOARDS",
    "google_jobs": "GOOGLE_JOBS_BOARDS",
    "recruitee": "RECRUITEE_BOARDS",
    "personio": "PERSONIO_BOARDS",
    "jazzhr": "JAZZHR_BOARDS",
    "beamery": "BEAMERY_BOARDS",
    "phenom": "PHENOM_BOARDS",
    "clearcompany": "CLEARCOMPANY_BOARDS",
    "adp": "ADP_BOARDS",
    "infor": "INFOR_BOARDS",
}

DEFAULT_TARGETS = ("lever", "selectminds", "successfactors", "generic_site")


@dataclass
class BoardResult:
    board_label: str
    url: str
    ok: bool
    error: str | None = None
    total_records: int = 0
    with_location: int = 0
    sample_locations: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass
class AdapterResult:
    adapter_name: str
    board_results: list[BoardResult] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return sum(b.total_records for b in self.board_results)

    @property
    def with_location(self) -> int:
        return sum(b.with_location for b in self.board_results)

    @property
    def pct(self) -> float:
        return 100.0 * self.with_location / self.total_records if self.total_records else 0.0

    @property
    def boards_ok(self) -> int:
        return sum(1 for b in self.board_results if b.ok)

    @property
    def boards_failed(self) -> int:
        return sum(1 for b in self.board_results if not b.ok)


# ── Setup ─────────────────────────────────────────────────────────────────


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def load_config_module(repo_root: Path):
    config_path = repo_root / "configs" / "config.py"
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find config file: {config_path}")
    module_name = "project_config_for_live_audit"
    spec = importlib.util.spec_from_file_location(module_name, config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module: {config_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _normalise_board_config(raw: Any, adapter_name: str) -> dict[str, Any]:
    """Convert a config-entry dict (or dataclass/object) into an adapter-ready dict.

    All adapters accept *some* subset of: job_board_url, url, slug, company.
    We set both job_board_url and url when possible so we don't have to
    special-case each adapter.
    """
    if hasattr(raw, "__dict__"):
        board = {k: v for k, v in raw.__dict__.items() if not k.startswith("_")}
    elif isinstance(raw, dict):
        board = dict(raw)
    else:
        board = {"url": str(raw)}

    url = board.get("url") or board.get("job_board_url") or board.get("api_endpoint")
    if url:
        board.setdefault("job_board_url", url)
        board.setdefault("url", url)

    # Per-adapter speed knobs.
    if adapter_name == "generic_site":
        board.setdefault("max_pages", 1)
        board.setdefault("scroll_rounds", 1)
        board.setdefault("page_timeout_ms", 30_000)
    if adapter_name == "successfactors":
        # Limit search-term iteration to just the empty "no-keyword" pass.
        # NOTE: must be [""] not []. The adapter does
        #   `source_config.get("search_terms") or DEFAULT_SEARCH_TERMS`
        # and [] is falsy in Python, so an empty list would silently fall
        # back to the default 7-term loop — 8 full page loads per board.
        # [""] is truthy; the adapter's `if str(term).strip()` filter then
        # drops the empty string, leaving only the single base pass.
        board.setdefault("search_terms", [""])
        board.setdefault("page_timeout_ms", 45_000)
    if adapter_name == "selectminds":
        board.setdefault("page_timeout_ms", 30_000)

    return board


def _board_label(board: dict[str, Any]) -> str:
    return (
        str(board.get("company"))
        or str(board.get("slug"))
        or str(board.get("url") or board.get("job_board_url") or "<unknown>")
    )


# ── Core ──────────────────────────────────────────────────────────────────


async def _audit_board(
    adapter: Any,
    raw_board: Any,
    adapter_name: str,
    sample_limit: int,
    board_timeout_s: float,
) -> BoardResult:
    board = _normalise_board_config(raw_board, adapter_name)
    label = _board_label(board)
    url = str(board.get("url") or board.get("job_board_url") or "")
    started = time.perf_counter()
    try:
        page = await asyncio.wait_for(adapter.discover(board), timeout=board_timeout_s)
    except asyncio.TimeoutError:
        return BoardResult(
            board_label=label,
            url=url,
            ok=False,
            error=f"timeout after {board_timeout_s:.0f}s",
            elapsed_s=time.perf_counter() - started,
        )
    except Exception as exc:
        return BoardResult(
            board_label=label,
            url=url,
            ok=False,
            error=f"{type(exc).__name__}: {exc}"[:200],
            elapsed_s=time.perf_counter() - started,
        )

    records = list(page.jobs)
    with_location = [r for r in records if r.location_raw]
    samples = [str(r.location_raw) for r in with_location[:5]]
    return BoardResult(
        board_label=label,
        url=url,
        ok=True,
        total_records=len(records),
        with_location=len(with_location),
        sample_locations=samples,
        elapsed_s=time.perf_counter() - started,
    )


async def audit_adapter(
    adapter_cls: type,
    boards: list[Any],
    *,
    boards_limit: int,
    sample_limit: int,
    board_timeout_s: float,
) -> AdapterResult:
    adapter_name = adapter_cls.adapter_name
    adapter = adapter_cls()
    selected = boards[:boards_limit]
    result = AdapterResult(adapter_name=adapter_name)

    for raw_board in selected:
        board_result = await _audit_board(
            adapter, raw_board, adapter_name, sample_limit, board_timeout_s
        )
        result.board_results.append(board_result)
    return result


# ── Rendering ─────────────────────────────────────────────────────────────


def render_table(results: list[AdapterResult]) -> None:
    if _RICH:
        _render_rich(results)
    else:
        _render_plain(results)


def _render_rich(results: list[AdapterResult]) -> None:
    console = Console()

    summary = Table(title="Live Location-Extraction Audit — per-adapter", show_lines=False)
    summary.add_column("Adapter", style="cyan")
    summary.add_column("Boards ok/fail", justify="right")
    summary.add_column("Records", justify="right")
    summary.add_column("w/ location", justify="right")
    summary.add_column("% populated", justify="right")
    summary.add_column("Sample", style="magenta")

    for r in results:
        all_samples: list[str] = []
        for b in r.board_results:
            all_samples.extend(b.sample_locations)
        pct_str = f"{r.pct:5.1f}%"
        if r.pct >= 80:
            pct_str = f"[green]{pct_str}[/green]"
        elif r.pct >= 30:
            pct_str = f"[yellow]{pct_str}[/yellow]"
        else:
            pct_str = f"[red]{pct_str}[/red]"
        summary.add_row(
            r.adapter_name,
            f"{r.boards_ok}/{r.boards_failed}",
            str(r.total_records),
            str(r.with_location),
            pct_str,
            ", ".join(all_samples[:3]) or "—",
        )
    console.print(summary)

    for r in results:
        detail = Table(title=f"{r.adapter_name} — per-board", show_lines=False)
        detail.add_column("Board", style="cyan", no_wrap=False)
        detail.add_column("OK?", justify="center")
        detail.add_column("Records", justify="right")
        detail.add_column("w/ loc", justify="right")
        detail.add_column("%", justify="right")
        detail.add_column("Time", justify="right")
        detail.add_column("Notes", style="dim")
        for b in r.board_results:
            pct = f"{(100.0 * b.with_location / b.total_records):.0f}%" if b.total_records else "—"
            ok_marker = "[green]✓[/green]" if b.ok else "[red]✗[/red]"
            note = ", ".join(b.sample_locations[:2]) if b.ok else (b.error or "")
            detail.add_row(
                b.board_label,
                ok_marker,
                str(b.total_records),
                str(b.with_location),
                pct,
                f"{b.elapsed_s:4.1f}s",
                note,
            )
        console.print(detail)


def _render_plain(results: list[AdapterResult]) -> None:
    print(f"{'adapter':<20} {'ok/fail':>8} {'records':>8} {'w/loc':>6} {'pct':>6}   sample")
    print("-" * 90)
    for r in results:
        samples: list[str] = []
        for b in r.board_results:
            samples.extend(b.sample_locations)
        print(
            f"{r.adapter_name:<20} "
            f"{r.boards_ok}/{r.boards_failed:<5} "
            f"{r.total_records:>8} {r.with_location:>6} "
            f"{r.pct:>5.1f}%   "
            f"{', '.join(samples[:3])}"
        )
    print()
    for r in results:
        print(f"\n# {r.adapter_name}")
        for b in r.board_results:
            status = "OK" if b.ok else "FAIL"
            print(
                f"  [{status}] {b.board_label:<35} "
                f"records={b.total_records:>3} loc={b.with_location:>3} "
                f"({b.elapsed_s:4.1f}s)  "
                f"{b.error or ', '.join(b.sample_locations[:2])}"
            )


def render_json(results: list[AdapterResult]) -> None:
    payload = [
        {
            "adapter": r.adapter_name,
            "total_records": r.total_records,
            "with_location": r.with_location,
            "pct_populated": round(r.pct, 2),
            "boards_ok": r.boards_ok,
            "boards_failed": r.boards_failed,
            "boards": [
                {
                    "label": b.board_label,
                    "url": b.url,
                    "ok": b.ok,
                    "error": b.error,
                    "total_records": b.total_records,
                    "with_location": b.with_location,
                    "sample_locations": b.sample_locations,
                    "elapsed_s": round(b.elapsed_s, 2),
                }
                for b in r.board_results
            ],
        }
        for r in results
    ]
    print(json.dumps(payload, indent=2))


# ── Entrypoint ────────────────────────────────────────────────────────────


async def _run(args: argparse.Namespace, repo_root: Path) -> list[AdapterResult]:
    add_repo_to_path(repo_root)

    # Import after path is set up.
    from vacancysoft.adapters import ADAPTER_REGISTRY  # noqa: WPS433

    config_module = load_config_module(repo_root)

    if args.all:
        target_names = sorted(ADAPTER_TO_CONFIG)
    elif args.only:
        target_names = list(args.only)
    else:
        target_names = list(DEFAULT_TARGETS)

    results: list[AdapterResult] = []
    for name in target_names:
        if name not in ADAPTER_REGISTRY:
            sys.stderr.write(f"skip {name}: not in ADAPTER_REGISTRY\n")
            continue
        config_attr = ADAPTER_TO_CONFIG.get(name)
        if not config_attr:
            sys.stderr.write(f"skip {name}: no config attr mapping\n")
            continue
        boards = getattr(config_module, config_attr, None)
        if not boards:
            sys.stderr.write(f"skip {name}: {config_attr} empty or missing\n")
            continue

        sys.stderr.write(f"auditing {name} ({min(args.boards, len(boards))} board(s))…\n")
        sys.stderr.flush()
        result = await audit_adapter(
            ADAPTER_REGISTRY[name],
            list(boards),
            boards_limit=args.boards,
            sample_limit=args.sample,
            board_timeout_s=args.board_timeout,
        )
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Live location-extraction audit.")
    parser.add_argument("--only", nargs="+", help="Audit only these adapter_name values.")
    parser.add_argument("--all", action="store_true", help="Audit every adapter in ADAPTER_TO_CONFIG.")
    parser.add_argument("--boards", type=int, default=2, help="Max boards per adapter (default 2).")
    parser.add_argument("--sample", type=int, default=20, help="Sample size cap per board (informational).")
    parser.add_argument("--board-timeout", type=float, default=90.0, help="Timeout per board, seconds.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    # Load .env so adapters needing API keys (reed, adzuna) see them.
    env_path = repo_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv(env_path)
        except ImportError:
            pass

    results = asyncio.run(_run(args, repo_root))
    if args.json:
        render_json(results)
    else:
        render_table(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
