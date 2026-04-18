"""Diagnostic probe: run the full successfactors adapter and dump everything.

Reveals which extraction path the 21 records came from and what the raw
payload / listing_payload looks like — so we can see why location_raw is 0%.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def setup() -> None:
    repo = Path(__file__).resolve().parents[1]
    src = repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def load_board() -> dict:
    repo = Path(__file__).resolve().parents[1]
    path = repo / "configs" / "config.py"
    spec = importlib.util.spec_from_file_location("_cfg_dbg", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore
    sys.modules["_cfg_dbg"] = module
    spec.loader.exec_module(module)  # type: ignore
    board = dict(module.SUCCESSFACTORS_BOARDS[0])
    board.setdefault("job_board_url", board.get("url"))
    board.setdefault("search_terms", [""])
    board.setdefault("page_timeout_ms", 45_000)
    return board


async def main() -> None:
    setup()
    board = load_board()
    print(f"[debug] board = {board['company']} → {board['url']}\n")

    from vacancysoft.adapters.successfactors import SuccessFactorsAdapter

    adapter = SuccessFactorsAdapter()
    page = await adapter.discover(board)

    print(f"[result] records={len(page.jobs)}")
    print(f"[diagnostics.counters]")
    for k, v in sorted(page.diagnostics.counters.items()):
        print(f"  {k}: {v}")

    print(f"\n[diagnostics.metadata keys]")
    for k in sorted(page.diagnostics.metadata):
        v = page.diagnostics.metadata[k]
        if isinstance(v, (list, dict)):
            print(f"  {k}: {type(v).__name__} len={len(v)}")
        else:
            print(f"  {k}: {str(v)[:120]}")

    # Dump any captured network URLs
    for k in ("term__network_urls", "term__post_search_anchor_samples"):
        if k in page.diagnostics.metadata:
            print(f"\n[{k}]")
            val = page.diagnostics.metadata[k]
            if isinstance(val, list):
                for item in val[:8]:
                    print(f"  {json.dumps(item) if isinstance(item, dict) else str(item)[:150]}")

    print("\n[first 3 records]")
    for i, r in enumerate(page.jobs[:3]):
        print(f"\n--- record {i} ---")
        print(f"  title_raw:      {r.title_raw}")
        print(f"  location_raw:   {r.location_raw}")
        print(f"  discovered_url: {r.discovered_url}")
        print(f"  listing_payload:{json.dumps(r.listing_payload, default=str)[:400]}")
        print(f"  provenance:     {json.dumps(r.provenance, default=str)[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
