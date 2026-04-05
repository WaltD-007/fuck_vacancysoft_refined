from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openpyxl import load_workbook


def _normalise_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        return None
    return text


KNOWN_SKIP_HOST_FRAGMENTS = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.",
    "jobs.google.com",
    "google.com",
}


def _looks_like_board(url: str) -> bool:
    lowered = url.lower()
    return not any(fragment in lowered for fragment in KNOWN_SKIP_HOST_FRAGMENTS)


def _classify_url(url: str) -> str:
    lowered = url.lower()
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "apply.workable.com" in host:
        return "workable"
    if "myworkdayjobs.com" in host:
        return "workday"
    if "eightfold.ai" in lowered or "eightfold" in lowered:
        return "eightfold"
    return "generic_site"


def _extract_greenhouse_slug(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0] == "boards" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def _extract_workable_slug(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host == "apply.workable.com" and parts:
        return parts[0]
    return None


def _company_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.split(":")[0]
    root = host.replace("www.", "").split(".")[0]
    cleaned = root.replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else url


def load_board_urls(xlsx_path: Path) -> dict[str, str]:
    workbook = load_workbook(filename=xlsx_path, read_only=True, data_only=True)
    sheet = workbook.active
    found: dict[str, str] = {}
    for row in sheet.iter_rows(min_row=1, values_only=True):
        if len(row) < 2:
            continue
        url = _normalise_url(row[1])
        if not url or not _looks_like_board(url):
            continue
        adapter_name = _classify_url(url)
        found.setdefault(adapter_name, url)
        if all(name in found for name in ["greenhouse", "workable", "workday", "eightfold", "generic_site"]):
            break
    return found


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def serialise_record(record: Any) -> dict[str, Any]:
    return {
        "title": getattr(record, "title_raw", None),
        "company": getattr(record, "provenance", {}).get("company") if getattr(record, "provenance", None) else None,
        "location": getattr(record, "location_raw", None),
        "posted_at": getattr(record, "posted_at_raw", None),
        "url": getattr(record, "discovered_url", None),
        "apply_url": getattr(record, "apply_url", None),
        "adapter": getattr(record, "provenance", {}).get("adapter") if getattr(record, "provenance", None) else None,
    }


async def run_smoke_tests(args: argparse.Namespace) -> dict[str, Any]:
    from vacancysoft.adapters import (
        EightfoldAdapter,
        GenericBrowserAdapter,
        GoogleJobsAdapter,
        GreenhouseAdapter,
        WorkableAdapter,
        WorkdayAdapter,
    )

    board_urls = load_board_urls(Path(args.xlsx))
    results: dict[str, Any] = {"source_urls": board_urls, "runs": {}}

    async def capture(name: str, coro: Any) -> None:
        try:
            page = await coro
            results["runs"][name] = {
                "ok": True,
                "job_count": len(page.jobs),
                "sample": [serialise_record(job) for job in page.jobs[: args.limit]],
                "diagnostics": {
                    "counters": dict(getattr(page.diagnostics, "counters", {})),
                    "warnings": list(getattr(page.diagnostics, "warnings", [])),
                    "errors": list(getattr(page.diagnostics, "errors", [])),
                    "metadata": dict(getattr(page.diagnostics, "metadata", {})),
                },
            }
        except Exception as exc:
            results["runs"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    greenhouse_url = board_urls.get("greenhouse")
    if greenhouse_url:
        slug = _extract_greenhouse_slug(greenhouse_url)
        if slug:
            await capture(
                "greenhouse",
                GreenhouseAdapter().discover(
                    {
                        "slug": slug,
                        "company": _company_name_from_url(greenhouse_url),
                        "job_board_url": greenhouse_url,
                        "timeout_seconds": args.timeout_seconds,
                    }
                ),
            )
        else:
            results["runs"]["greenhouse"] = {"ok": False, "error": f"Could not derive Greenhouse slug from {greenhouse_url}"}

    workable_url = board_urls.get("workable")
    if workable_url:
        slug = _extract_workable_slug(workable_url)
        if slug:
            await capture(
                "workable",
                WorkableAdapter().discover(
                    {
                        "slug": slug,
                        "company": _company_name_from_url(workable_url),
                        "job_board_url": workable_url,
                        "timeout_seconds": args.timeout_seconds,
                    }
                ),
            )
        else:
            results["runs"]["workable"] = {"ok": False, "error": f"Could not derive Workable slug from {workable_url}"}

    workday_url = board_urls.get("workday")
    if workday_url:
        adapter = WorkdayAdapter()
        try:
            _endpoint, page = await adapter.discover_from_board_url(
                job_board_url=workday_url,
                limit=max(args.limit, 2),
            )
            results["runs"]["workday"] = {
                "ok": True,
                "job_count": len(page.jobs),
                "sample": [serialise_record(job) for job in page.jobs[: args.limit]],
                "diagnostics": {
                    "counters": dict(getattr(page.diagnostics, "counters", {})),
                    "warnings": list(getattr(page.diagnostics, "warnings", [])),
                    "errors": list(getattr(page.diagnostics, "errors", [])),
                    "metadata": dict(getattr(page.diagnostics, "metadata", {})),
                },
            }
        except Exception as exc:
            results["runs"]["workday"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    eightfold_url = board_urls.get("eightfold")
    if eightfold_url:
        await capture(
            "eightfold",
            EightfoldAdapter().discover(
                {
                    "job_board_url": eightfold_url,
                    "company": _company_name_from_url(eightfold_url),
                    "search_terms": args.search_terms,
                    "page_timeout_ms": args.page_timeout_ms,
                    "search_settle_ms": args.search_settle_ms,
                }
            ),
        )

    generic_url = board_urls.get("generic_site")
    if generic_url:
        await capture(
            "generic_site",
            GenericBrowserAdapter().discover(
                {
                    "job_board_url": generic_url,
                    "company": _company_name_from_url(generic_url),
                    "search_terms": args.search_terms,
                    "page_timeout_ms": args.page_timeout_ms,
                    "wait_after_nav_ms": args.search_settle_ms,
                }
            ),
        )

    await capture(
        "google_jobs",
        GoogleJobsAdapter().discover(
            {
                "search_terms": args.google_queries,
                "timeout_seconds": args.timeout_seconds,
                "max_pages_per_query": 1,
            }
        ),
    )

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test live scraper adapters against live job boards")
    parser.add_argument("--repo-root", default=".", help="Path to repository root")
    parser.add_argument("--xlsx", default="Jobs_2026_03_31_clean.xlsx", help="Workbook with board URLs in column B")
    parser.add_argument("--limit", type=int, default=2, help="Number of sample leads to print per adapter")
    parser.add_argument("--timeout-seconds", type=float, default=20.0, help="HTTP timeout for API adapters")
    parser.add_argument("--page-timeout-ms", type=int, default=30000, help="Browser timeout for Playwright adapters")
    parser.add_argument("--search-settle-ms", type=int, default=2000, help="Browser wait after search/navigation")
    parser.add_argument(
        "--search-term",
        dest="search_terms",
        action="append",
        default=None,
        help="Search term override for browser adapters. Repeat to pass multiple values.",
    )
    parser.add_argument(
        "--google-query",
        dest="google_queries",
        action="append",
        default=None,
        help="Google Jobs query override. Repeat to pass multiple values.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    add_repo_to_path(repo_root)

    if args.search_terms is None:
        args.search_terms = ["risk", "quant"]
    if args.google_queries is None:
        args.google_queries = ["risk manager finance", "quantitative analyst finance"]

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.is_absolute():
        xlsx_path = repo_root / xlsx_path
    args.xlsx = str(xlsx_path)

    if not xlsx_path.exists():
        raise SystemExit(f"Workbook not found: {xlsx_path}")

    results = asyncio.run(run_smoke_tests(args))
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
