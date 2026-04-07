"""
Quick test of GenericBrowserAdapter against a list of career-site URLs.

Usage (from project root):
    python3 scripts/generic_waterfall_test.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from vacancysoft.adapters.generic_browser import GenericBrowserAdapter


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_root = Path(__file__).resolve().parent.parent
_load_env_file(_root / ".env")
_load_env_file(_root / "alembic" / "env")

SITES_TO_TEST = [
    {"url": "https://careers.enstargroup.com", "company": "Enstar Group"},
    # Arbuthnot Latham uses Eploy ATS (careers.arbuthnotlatham.co.uk) behind Cloudflare.
    # Generic browser is blocked (403). Needs a dedicated Eploy adapter.
    # {"url": "https://careers.arbuthnotlatham.co.uk/vacancies", "company": "Arbuthnot Latham"},
]

PAGE_TIMEOUT_MS = 30_000
WAIT_AFTER_NAV_MS = 2_000


async def test_site(entry: dict) -> None:
    url = entry["url"]
    company = entry.get("company") or urlparse(url).netloc
    print(f"\n--- {company} ---")
    print(f"URL: {url}")

    adapter = GenericBrowserAdapter()
    try:
        page = await adapter.discover(
            {
                "job_board_url": url,
                "company": company,
                "page_timeout_ms": PAGE_TIMEOUT_MS,
                "wait_after_nav_ms": WAIT_AFTER_NAV_MS,
            }
        )
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return

    jobs = page.jobs
    diag = page.diagnostics
    http_status = diag.metadata.get("http_status")

    print(f"HTTP status: {http_status}")
    print(f"Jobs found: {len(jobs)}")
    print(f"Selector used: {diag.metadata.get('last_selector_used', 'none')}")

    if http_status and http_status not in (200, 301, 302):
        print(f"WARNING: Non-200 response — site may be blocking scrapers (status {http_status})")

    if diag.warnings:
        print(f"Warnings: {diag.warnings}")
    if diag.errors:
        print(f"Errors: {diag.errors}")

    for job in jobs[:5]:
        print(f"  - {job.title_raw or '(no title)'} | {job.discovered_url}")

    if len(jobs) > 5:
        print(f"  ... and {len(jobs) - 5} more")


async def main() -> None:
    for site in SITES_TO_TEST:
        await test_site(site)
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
