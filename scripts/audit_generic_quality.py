from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def load_generic_browser_boards(repo_root: Path) -> list[dict]:
    config_path = repo_root / "configs" / "config.py"
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find config file: {config_path}")

    module_name = "project_config_for_audit"
    spec = importlib.util.spec_from_file_location(module_name, config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load config module from: {config_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    boards = getattr(module, "GENERIC_BROWSER_BOARDS", None)
    if boards is None:
        raise AttributeError(f"GENERIC_BROWSER_BOARDS not found in: {config_path}")
    return boards


def clean(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


async def main() -> None:
    repo_root = Path(".").resolve()
    add_repo_to_path(repo_root)

    from vacancysoft.adapters import GenericBrowserAdapter

    all_boards = load_generic_browser_boards(repo_root)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(all_boards)
    boards = all_boards[:limit]

    results = []

    for board in boards:
        company = board["company"]
        url = board["url"]
        console.print(f"[dim]Auditing {company}[/dim]")

        try:
            page = await GenericBrowserAdapter().discover(
                {
                    "job_board_url": url,
                    "company": company,
                    "page_timeout_ms": 30000,
                    "wait_after_nav_ms": 2000,
                }
            )

            diagnostics = getattr(page, "diagnostics", None)
            metadata = dict(getattr(diagnostics, "metadata", {})) if diagnostics else {}
            counters = dict(getattr(diagnostics, "counters", {})) if diagnostics else {}

            sample_titles = [getattr(job, "title_raw", None) for job in page.jobs[:2]]

            verdict = "ok"
            if len(page.jobs) == 0:
                verdict = "empty"
            elif sample_titles and any(
                t and any(
                    bad in t.lower()
                    for bad in ("saved jobs", "talent community", "job search", "clear all", "view all jobs")
                )
                for t in sample_titles
            ):
                verdict = "noisy"

            results.append(
                {
                    "company": company,
                    "url": url,
                    "verdict": verdict,
                    "job_count": len(page.jobs),
                    "listings_seen": counters.get("listings_seen", 0),
                    "unique_urls": counters.get("unique_urls", 0),
                    "last_selector_used": metadata.get("last_selector_used", ""),
                    "sample_titles": sample_titles,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "company": company,
                    "url": url,
                    "verdict": "error",
                    "job_count": 0,
                    "listings_seen": 0,
                    "unique_urls": 0,
                    "last_selector_used": "",
                    "sample_titles": [f"{type(exc).__name__}: {exc}"],
                }
            )

    table = Table(title=f"Generic audit ({len(results)} sites)")
    table.add_column("Company")
    table.add_column("Verdict")
    table.add_column("Jobs", justify="right")
    table.add_column("Listings", justify="right")
    table.add_column("Unique", justify="right")
    table.add_column("Selector")
    table.add_column("Sample")

    for row in results:
        colour = {
            "ok": "green",
            "empty": "yellow",
            "noisy": "magenta",
            "error": "red",
        }.get(row["verdict"], "white")

        table.add_row(
            row["company"],
            f"[{colour}]{row['verdict']}[/{colour}]",
            str(row["job_count"]),
            str(row["listings_seen"]),
            str(row["unique_urls"]),
            clean(row["last_selector_used"], 30),
            clean(" | ".join(t for t in row["sample_titles"] if t), 100),
        )

    console.print()
    console.print(table)

    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    console.print(f"\n[bold]Verdict counts:[/bold] {counts}")

    out_path = repo_root / "generic_audit_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[dim]Wrote detailed results to {out_path}[/dim]")


if __name__ == "__main__":
    asyncio.run(main())