from __future__ import annotations

from vacancysoft.adapters.base import SourceAdapter


async def run_discovery(adapter: SourceAdapter, source_config: dict) -> int:
    """Run a single discovery pass and return the number of records seen."""
    page = await adapter.discover(source_config=source_config)
    return len(page.jobs)
