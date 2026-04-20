#!/usr/bin/env python3.13
"""Quick test of all SuccessFactors boards — run each with a short timeout and report results."""

import asyncio
import sys
import time

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import Source
from sqlalchemy import select


async def test_one(src, timeout_s=120):
    """Test a single SuccessFactors source. Returns (name, jobs, elapsed, error)."""
    from vacancysoft.adapters import ADAPTER_REGISTRY

    adapter_cls = ADAPTER_REGISTRY.get(src.adapter_name)
    if not adapter_cls:
        return (src.employer_name, 0, 0, f"No adapter: {src.adapter_name}")

    adapter = adapter_cls()
    config = {**(src.config_blob or {}), "company": src.employer_name}

    start = time.perf_counter()
    try:
        result = await asyncio.wait_for(adapter.discover(config), timeout=timeout_s)
        elapsed = time.perf_counter() - start
        jobs = len(result.jobs) if result and result.jobs else 0
        meta = result.diagnostics.metadata if result else {}
        method = meta.get("method", "")
        return (src.employer_name, jobs, elapsed, None, method)
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        return (src.employer_name, 0, elapsed, f"TIMEOUT after {elapsed:.0f}s", "")
    except Exception as e:
        elapsed = time.perf_counter() - start
        return (src.employer_name, 0, elapsed, f"{type(e).__name__}: {e}", "")


async def main():
    timeout_s = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    print(f"Testing SuccessFactors boards (timeout: {timeout_s}s per board)\n")
    print(f"{'Company':35s} | {'Jobs':>5s} | {'Time':>6s} | {'Method':15s} | Status")
    print("-" * 100)

    with SessionLocal() as s:
        sources = list(s.execute(
            select(Source).where(Source.adapter_name == "successfactors", Source.active.is_(True))
        ).scalars())

    total_jobs = 0
    ok = 0
    fail = 0

    for src in sources:
        result = await test_one(src, timeout_s)
        name, jobs, elapsed, error = result[0], result[1], result[2], result[3]
        method = result[4] if len(result) > 4 else ""

        if error:
            fail += 1
            print(f"{name:35s} | {jobs:5d} | {elapsed:5.1f}s | {'':15s} | FAIL: {error[:50]}")
        else:
            ok += 1
            total_jobs += jobs
            print(f"{name:35s} | {jobs:5d} | {elapsed:5.1f}s | {method:15s} | OK")

    print("-" * 100)
    print(f"Done: {ok} OK, {fail} FAIL, {total_jobs} total jobs")


if __name__ == "__main__":
    asyncio.run(main())
