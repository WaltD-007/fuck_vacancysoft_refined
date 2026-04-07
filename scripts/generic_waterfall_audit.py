from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from rich.console import Console
from rich.table import Table

console = Console()

SMART_SCRAPE_URL = "https://production-sfo.browserless.io/smart-scrape"
BLOCK_TEXT_FRAGMENTS = (
    "403",
    "forbidden",
    "access is denied",
    "access denied",
    "blocked",
    "captcha",
    "unauthorized",
)

NOISY_TITLE_FRAGMENTS = (
    "saved jobs",
    "talent community",
    "job search",
    "clear all",
    "view all jobs",
    "consent",
    "cookie",
    "privacy",
    "register",
    "login",
    "log in",
)

NON_JOB_HREF_FRAGMENTS = (
    "/cookie",
    "/privacy",
    "/terms",
    "/faq",
    "/about",
    "/contact",
    "/culture",
    "/benefits",
    "/team",
    "/teams",
    "/departments",
    "/talent-community",
    "/job-alert",
    "/login",
    "/account",
    "/working-for-us",
    "/discover",
    "/key-teams",
    "/early-careers",
    "/graduates",
    "/internships",
    "/students",
    "/leadership",
    "/board-of-directors",
    "/work-at",
    "/life-at",
    "/what-we-can-offer",
    "javascript:",
    "mailto:",
    "tel:",
    "#",
)

JOBISH_HREF_FRAGMENTS = (
    "/job",
    "jobdetail",
    "job-detail",
    "job_detail",
    "/jobs/",
    "/vacanc",
    "/position",
    "/posting",
    "/requisition",
    "/opening",
    "vacancydetails",
    "jobid=",
    "reqid=",
    "requisitionid",
)

PRIORITY_EMPTY_COMPANIES = {
    # Add companies here later if you want Smart Scrape rescue for selected empties.
    # Example:
    # "Lloyds Banking Group",
    # "UBS",
}


def add_repo_to_path(repo_root: Path) -> None:
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def load_generic_browser_boards(repo_root: Path) -> list[dict]:
    config_path = repo_root / "configs" / "config.py"
    if not config_path.exists():
        raise FileNotFoundError(f"Could not find config file: {config_path}")

    module_name = "project_config_for_waterfall_audit"
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


def load_key_value_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def clean(text: str | None, limit: int = 100) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def same_domain(url: str, board_url: str) -> bool:
    try:
        return urlparse(url).netloc.lower() == urlparse(board_url).netloc.lower()
    except Exception:
        return False


def looks_like_job_url(url: str) -> bool:
    lowered = url.lower()
    if any(fragment in lowered for fragment in NON_JOB_HREF_FRAGMENTS):
        return False
    return any(token in lowered for token in JOBISH_HREF_FRAGMENTS)


def classify_browser_result(run: dict[str, Any]) -> str:
    diagnostics = run.get("diagnostics", {})
    metadata = diagnostics.get("metadata", {}) or {}
    errors = diagnostics.get("errors", []) or []
    job_count = int(run.get("job_count", 0))
    sample = run.get("sample", []) or []

    status = metadata.get("http_status")
    final_url = str(metadata.get("final_url", "") or "")
    title_blob = " ".join(str(item.get("title") or "") for item in sample[:3]).lower()
    error_blob = " ".join(str(e) for e in errors).lower()
    combined = f"{final_url}\n{title_blob}\n{error_blob}".lower()

    if status in (401, 403) or any(fragment in combined for fragment in BLOCK_TEXT_FRAGMENTS):
        return "blocked"
    if run.get("ok") is False:
        return "error"
    if job_count == 0:
        return "empty"
    if any(any(fragment in str(item.get("title") or "").lower() for fragment in NOISY_TITLE_FRAGMENTS) for item in sample):
        return "noisy"
    return "good"


def should_smart_scrape(company: str, browser_classification: str, rescue_empty: bool) -> bool:
    if browser_classification == "blocked":
        return True
    if browser_classification == "empty" and rescue_empty and company in PRIORITY_EMPTY_COMPANIES:
        return True
    return False


async def smart_scrape_fetch_html(board_url: str) -> str:
    token = os.getenv("BROWSERLESS_TOKEN", "").strip()
    ws_url = os.getenv("BROWSERLESS_WS_URL", "").strip()

    if token:
        smart_scrape_endpoint = f"{SMART_SCRAPE_URL}?timeout=60000&token={token}"
    elif ws_url and "token=" in ws_url:
        token = ws_url.split("token=", 1)[1].split("&", 1)[0]
        smart_scrape_endpoint = f"{SMART_SCRAPE_URL}?timeout=60000&token={token}"
    else:
        raise RuntimeError("No Browserless token found. Set BROWSERLESS_TOKEN or BROWSERLESS_WS_URL.")

    payload = {
        "url": board_url,
        "formats": ["html"],
    }

    import urllib.request

    req = urllib.request.Request(
        smart_scrape_endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _fetch() -> str:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read().decode("utf-8", errors="replace")

    return await asyncio.to_thread(_fetch)


def extract_html_from_smart_scrape_response(raw_text: str) -> str:
    raw_text = raw_text.strip()
    if not raw_text:
        return ""
    try:
        data = json.loads(raw_text)
    except Exception:
        return raw_text

    if isinstance(data, dict):
        if isinstance(data.get("html"), str):
            return data["html"]
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("html"), str):
            return result["html"]
        if isinstance(data.get("data"), dict) and isinstance(data["data"].get("html"), str):
            return data["data"]["html"]
    return raw_text


def extract_candidate_urls_from_html(html: str, board_url: str) -> list[dict[str, str | None]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    results: list[dict[str, str | None]] = []

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(board_url, href)
        if not same_domain(url, board_url):
            continue
        if not looks_like_job_url(url):
            continue

        title = " ".join(a.get_text(" ", strip=True).split())
        lowered_title = title.lower()

        if any(fragment in lowered_title for fragment in NOISY_TITLE_FRAGMENTS):
            continue

        if url in seen:
            continue
        seen.add(url)

        results.append(
            {
                "title": title or None,
                "url": url,
                "href": href,
                "selector": "smart_scrape_html",
            }
        )

    return results


async def run_browser_pass(company: str, board_url: str) -> dict[str, Any]:
    from vacancysoft.adapters import GenericBrowserAdapter

    try:
        page = await GenericBrowserAdapter().discover(
            {
                "job_board_url": board_url,
                "company": company,
                "page_timeout_ms": 30000,
                "wait_after_nav_ms": 2000,
            }
        )
        return {
            "ok": True,
            "job_count": len(page.jobs),
            "sample": [
                {
                    "title": getattr(job, "title_raw", None),
                    "url": getattr(job, "discovered_url", None),
                }
                for job in page.jobs[:3]
            ],
            "diagnostics": {
                "counters": dict(getattr(page.diagnostics, "counters", {})),
                "warnings": list(getattr(page.diagnostics, "warnings", [])),
                "errors": list(getattr(page.diagnostics, "errors", [])),
                "metadata": dict(getattr(page.diagnostics, "metadata", {})),
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "job_count": 0,
            "sample": [],
            "diagnostics": {
                "counters": {},
                "warnings": [],
                "errors": [f"{type(exc).__name__}: {exc}"],
                "metadata": {},
            },
        }


async def run_smart_scrape_pass(company: str, board_url: str) -> dict[str, Any]:
    try:
        raw = await smart_scrape_fetch_html(board_url)
        html = extract_html_from_smart_scrape_response(raw)
        candidates = extract_candidate_urls_from_html(html, board_url)
        return {
            "ok": True,
            "job_count": len(candidates),
            "sample": candidates[:3],
            "diagnostics": {
                "counters": {
                    "listings_seen": len(candidates),
                    "unique_urls": len(candidates),
                },
                "warnings": [],
                "errors": [],
                "metadata": {
                    "board_url": board_url,
                    "company": company,
                    "mode": "smart_scrape_rescue",
                },
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "job_count": 0,
            "sample": [],
            "diagnostics": {
                "counters": {},
                "warnings": [],
                "errors": [f"{type(exc).__name__}: {exc}"],
                "metadata": {
                    "board_url": board_url,
                    "company": company,
                    "mode": "smart_scrape_rescue",
                },
            },
        }


async def main() -> None:
    repo_root = Path(".").resolve()
    add_repo_to_path(repo_root)

    load_key_value_env_file(repo_root / ".env")
    load_key_value_env_file(repo_root / "alembic" / "env")

    all_boards = load_generic_browser_boards(repo_root)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(all_boards)
    rescue_empty = "--rescue-empty" in sys.argv
    boards = all_boards[:limit]

    results = []

    for board in boards:
        company = board["company"]
        board_url = board["url"]

        console.print(f"[dim]Processing {company}[/dim]")

        browser_run = await run_browser_pass(company, board_url)
        browser_classification = classify_browser_result(browser_run)

        smart_scrape_used = False
        smart_scrape_run = None
        final_run = browser_run
        final_classification = browser_classification
        strategy = "browser"

        if should_smart_scrape(company, browser_classification, rescue_empty):
            smart_scrape_used = True
            strategy = "smart_scrape_rescue"
            smart_scrape_run = await run_smart_scrape_pass(company, board_url)

            if smart_scrape_run.get("ok") and int(smart_scrape_run.get("job_count", 0)) > 0:
                final_run = smart_scrape_run
                final_classification = "rescued"
            else:
                final_run = browser_run
                final_classification = browser_classification

        diagnostics = final_run.get("diagnostics", {})
        counters = diagnostics.get("counters", {}) or {}
        metadata = diagnostics.get("metadata", {}) or {}
        sample = final_run.get("sample", []) or []

        results.append(
            {
                "company": company,
                "url": board_url,
                "strategy": strategy,
                "browser_classification": browser_classification,
                "final_classification": final_classification,
                "smart_scrape_used": smart_scrape_used,
                "job_count": int(final_run.get("job_count", 0)),
                "listings_seen": int(counters.get("listings_seen", 0)),
                "unique_urls": int(counters.get("unique_urls", 0)),
                "selector": metadata.get("last_selector_used", metadata.get("mode", "")),
                "sample_titles": [item.get("title") for item in sample[:2]],
                "browser_run": browser_run,
                "smart_scrape_run": smart_scrape_run,
            }
        )

    table = Table(title=f"Generic waterfall audit ({len(results)} sites)")
    table.add_column("Company")
    table.add_column("Browser")
    table.add_column("Final")
    table.add_column("Strategy")
    table.add_column("Jobs", justify="right")
    table.add_column("Listings", justify="right")
    table.add_column("Unique", justify="right")
    table.add_column("Selector")
    table.add_column("Sample")

    for row in results:
        colour = {
            "good": "green",
            "rescued": "cyan",
            "blocked": "red",
            "empty": "yellow",
            "noisy": "magenta",
            "error": "red",
        }.get(row["final_classification"], "white")

        table.add_row(
            row["company"],
            row["browser_classification"],
            f"[{colour}]{row['final_classification']}[/{colour}]",
            row["strategy"],
            str(row["job_count"]),
            str(row["listings_seen"]),
            str(row["unique_urls"]),
            clean(str(row["selector"]), 24),
            clean(" | ".join(t for t in row["sample_titles"] if t), 100),
        )

    console.print()
    console.print(table)

    counts: dict[str, int] = {}
    for row in results:
        counts[row["final_classification"]] = counts.get(row["final_classification"], 0) + 1
    console.print(f"\n[bold]Final classification counts:[/bold] {counts}")

    out_path = repo_root / "generic_waterfall_audit_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[dim]Wrote detailed results to {out_path}[/dim]")


if __name__ == "__main__":
    asyncio.run(main())