from __future__ import annotations

from vacancysoft.adapters.base import SourceAdapter


async def run_discovery(adapter: SourceAdapter, source_config: dict) -> int:
    """Run a single discovery pass and return the number of records seen.

    This is a starter stub. The full implementation should:
    - create a SourceRun
    - persist ExtractionAttempt records
    - upsert RawJob records immediately
    - update SourceHealth after completion
    """
    page = await adapter.discover(source_config=source_config)
    return len(page.jobs)
