"""
Quick test of GenericBrowserAdapter against a list of career-site URLs.

Usage (from project root):
    python3 scripts/generic_waterfall_test.py
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from vacancysoft.adapters.generic_browser import GenericBrowserAdapter

SITES_TO_TEST = [
    {"url": "https://careers.enstargroup.com", "company": "Enstar Group"},
    {"url": "https://careers.arbuthnotlatham.co.uk/vacancies", "company": "Arbuthnot Latham"},
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

    print(f"Jobs found: {len(jobs)}")
    print(f"Selector used: {diag.metadata.get('last_selector_used', 'none')}")

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
