"""Static audit of location extraction across all adapters.

Scans each adapter module under src/vacancysoft/adapters/ using AST — no
imports, so this runs even when adapter dependencies (Playwright, httpx, etc.)
aren't installed.

For every call to ``DiscoveredJobRecord(...)`` inside an adapter, the auditor
inspects the ``location_raw=`` argument and classifies the adapter as:

* ``scrapes``  — at least one constructor passes a non-``None`` expression.
* ``none``     — every constructor passes the literal ``None`` (pure enricher
                 dependency).
* ``mixed``    — some constructors pass a value, others pass ``None``.
* ``no-calls`` — no ``DiscoveredJobRecord`` constructor was found (unusual).

Usage
-----
    python3 scripts/audit_adapter_locations.py
    python3 scripts/audit_adapter_locations.py --json
    python3 scripts/audit_adapter_locations.py --adapters-dir path/to/adapters
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    _RICH = True
except ImportError:  # pragma: no cover — rich is optional
    _RICH = False


EXCLUDE_FILENAMES = {"__init__.py", "base.py"}


@dataclass
class AdapterReport:
    name: str
    path: Path
    construct_calls: int = 0
    location_none_count: int = 0
    location_expr_count: int = 0
    location_expressions: list[str] = field(default_factory=list)
    location_sources: set[str] = field(default_factory=set)

    @property
    def status(self) -> str:
        if self.construct_calls == 0:
            return "no-calls"
        if self.location_expr_count == 0:
            return "none"
        if self.location_none_count == 0:
            return "scrapes"
        return "mixed"

    @property
    def status_icon(self) -> str:
        return {
            "scrapes": "[green]YES[/green]" if _RICH else "YES",
            "mixed": "[yellow]PARTIAL[/yellow]" if _RICH else "PARTIAL",
            "none": "[red]NO[/red]" if _RICH else "NO",
            "no-calls": "[dim]n/a[/dim]" if _RICH else "n/a",
        }[self.status]


# ── AST helpers ────────────────────────────────────────────────────────────


def _is_discovered_record_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "DiscoveredJobRecord":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "DiscoveredJobRecord":
        return True
    return False


def _expr_text(expr: ast.AST, source: str) -> str:
    try:
        return ast.get_source_segment(source, expr) or ast.unparse(expr)
    except Exception:  # pragma: no cover
        return "<unprintable>"


def _classify_location_sources(expr_text: str) -> set[str]:
    """Heuristic labels for where the adapter pulls location from."""
    lowered = expr_text.lower()
    tags: set[str] = set()
    if "json" in lowered or ".get(" in lowered and "posting" in lowered:
        tags.add("json-api")
    if any(tok in lowered for tok in ("inner_text", "query_selector", "get_attribute", "dom", "sniff")):
        tags.add("dom")
    if "ld_json" in lowered or "ld-json" in lowered or "jobposting" in lowered:
        tags.add("json-ld")
    if "xml" in lowered or "rss" in lowered or "etree" in lowered:
        tags.add("xml-rss")
    if "location" in lowered and "city" in lowered and "country" in lowered:
        tags.add("composite")
    if "workplacetype" in lowered:
        tags.add("workplace-type")
    if not tags:
        tags.add("value")
    return tags


# ── Core auditor ──────────────────────────────────────────────────────────


def audit_file(path: Path) -> AdapterReport:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    report = AdapterReport(name=path.stem, path=path)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_discovered_record_call(node):
            continue
        report.construct_calls += 1

        location_expr: ast.AST | None = None
        for kw in node.keywords:
            if kw.arg == "location_raw":
                location_expr = kw.value
                break

        if location_expr is None:
            # Positional form: rare but possible — treat as unknown/None-like
            report.location_none_count += 1
            continue

        if isinstance(location_expr, ast.Constant) and location_expr.value is None:
            report.location_none_count += 1
            continue

        report.location_expr_count += 1
        text = _expr_text(location_expr, source)
        report.location_expressions.append(text)
        report.location_sources.update(_classify_location_sources(text))

    return report


def audit_directory(adapters_dir: Path) -> list[AdapterReport]:
    reports: list[AdapterReport] = []
    for path in sorted(adapters_dir.glob("*.py")):
        if path.name in EXCLUDE_FILENAMES:
            continue
        try:
            reports.append(audit_file(path))
        except SyntaxError as exc:
            sys.stderr.write(f"skip {path.name}: syntax error ({exc})\n")
    return reports


# ── Rendering ────────────────────────────────────────────────────────────


def render_table(reports: list[AdapterReport]) -> None:
    if not _RICH:
        _render_plain(reports)
        return

    console = Console()
    table = Table(title="Adapter Location Extraction Audit", show_lines=False)
    table.add_column("Adapter", style="cyan", no_wrap=True)
    table.add_column("Scrapes?", justify="center")
    table.add_column("Ctor calls", justify="right")
    table.add_column("w/ location", justify="right")
    table.add_column("w/ None", justify="right")
    table.add_column("Sources", style="magenta")

    for r in reports:
        table.add_row(
            r.name,
            r.status_icon,
            str(r.construct_calls),
            str(r.location_expr_count),
            str(r.location_none_count),
            ",".join(sorted(r.location_sources)) or "—",
        )

    console.print(table)
    _render_summary(reports, console)


def _render_plain(reports: list[AdapterReport]) -> None:
    header = f"{'adapter':<25} {'status':<10} {'ctors':>5} {'loc':>5} {'none':>5}  sources"
    print(header)
    print("-" * len(header))
    for r in reports:
        print(
            f"{r.name:<25} {r.status:<10} "
            f"{r.construct_calls:>5} {r.location_expr_count:>5} {r.location_none_count:>5}  "
            f"{','.join(sorted(r.location_sources)) or '—'}"
        )
    _render_summary(reports, None)


def _render_summary(reports: list[AdapterReport], console: "Console | None") -> None:
    total = len(reports)
    scrapes = sum(1 for r in reports if r.status == "scrapes")
    mixed = sum(1 for r in reports if r.status == "mixed")
    none = sum(1 for r in reports if r.status == "none")
    no_calls = sum(1 for r in reports if r.status == "no-calls")
    relies_on_enricher = none + mixed

    lines = [
        "",
        f"total adapters : {total}",
        f"fully scrapes  : {scrapes}",
        f"partial scrape : {mixed}",
        f"no location    : {none}",
        f"no-calls       : {no_calls}",
        f"enricher-bound : {relies_on_enricher}  ({relies_on_enricher/total:.0%} of total)"
        if total else "enricher-bound : 0",
    ]

    if console:
        console.print("\n".join(lines))
    else:
        print("\n".join(lines))


def render_json(reports: list[AdapterReport]) -> None:
    payload = [
        {
            "adapter": r.name,
            "path": str(r.path),
            "status": r.status,
            "construct_calls": r.construct_calls,
            "location_expr_count": r.location_expr_count,
            "location_none_count": r.location_none_count,
            "sources": sorted(r.location_sources),
            "expressions": r.location_expressions,
        }
        for r in reports
    ]
    print(json.dumps(payload, indent=2))


# ── CLI ───────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Static location-extraction audit for adapters.")
    parser.add_argument(
        "--adapters-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "vacancysoft" / "adapters",
        help="Directory of adapter modules (default: src/vacancysoft/adapters).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table.")
    parser.add_argument(
        "--only",
        nargs="+",
        help="Restrict audit to the listed adapter names (stem only, e.g. lever selectminds).",
    )
    args = parser.parse_args()

    if not args.adapters_dir.is_dir():
        parser.error(f"adapters-dir not found: {args.adapters_dir}")

    reports = audit_directory(args.adapters_dir)
    if args.only:
        wanted = set(args.only)
        reports = [r for r in reports if r.name in wanted]
        if not reports:
            parser.error(f"no adapters matched --only {args.only}")

    if args.json:
        render_json(reports)
    else:
        render_table(reports)

    return 0


if __name__ == "__main__":
    sys.exit(main())
