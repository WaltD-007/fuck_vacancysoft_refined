"""Map an (employer, adapter, url) triple to one of ~30 sector buckets.

The sector taxonomy lives in ``configs/sector_taxonomy.yaml`` and
contains:

- ``allowed_sectors``: enum-like list of valid sector keys.
- ``employers``: explicit canonical_company_key → sector mapping.
- ``patterns``: regex fallback rules (first match wins).
- ``deny_pattern_match``: kill-list — these keys never match patterns.

Resolution order (the first match wins):

1. **Aggregator adapter override**: if the adapter is one of the
   aggregator adapters (adzuna, reed, …) the sector is always
   ``aggregator`` — no further checks.
2. **Explicit employer mapping**: the slugified employer_name is
   looked up in ``employers``.
3. **Pattern fallback**: regex over the raw ``employer_name``,
   skipping any key in ``deny_pattern_match``.
4. **Default**: ``unknown``.

The YAML is loaded once per process and cached. Operators reload via
process restart (or by calling ``invalidate_cache()`` from a script).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


# Path resolution: the YAML lives at ``<repo>/configs/sector_taxonomy.yaml``.
# This file lives at ``<repo>/src/vacancysoft/source_registry/sector_classifier.py``
# so we walk up four parents to reach the repo root.
_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "sector_taxonomy.yaml"
)

# Aggregator adapters whose presence forces sector='aggregator' regardless
# of employer name. Mirrors the list in vacancysoft.api.ledger; kept in
# sync manually because importing ledger here would create a cycle.
_AGGREGATOR_ADAPTERS = frozenset(
    {"adzuna", "reed", "google_jobs", "efinancialcareers", "coresignal"}
)

# In-process cache. Reloaded by invalidate_cache() or process restart.
_taxonomy_cache: dict | None = None
_compiled_patterns: list[tuple[re.Pattern[str], str]] | None = None


def _slugify(value: str) -> str:
    """Match the slug rule used by config_seed_loader for source_keys."""
    return "_".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in (value or "")).split()
    )


def _load_taxonomy() -> dict:
    global _taxonomy_cache, _compiled_patterns
    if _taxonomy_cache is None:
        with open(_TAXONOMY_PATH, encoding="utf-8") as f:
            _taxonomy_cache = yaml.safe_load(f) or {}
        _compiled_patterns = [
            (re.compile(rule["regex"], re.IGNORECASE), rule["sector"])
            for rule in (_taxonomy_cache.get("patterns") or [])
        ]
    return _taxonomy_cache


def invalidate_cache() -> None:
    """Drop the in-process taxonomy cache. Call after editing the YAML
    in-place during a long-running process (tests, dev server)."""
    global _taxonomy_cache, _compiled_patterns
    _taxonomy_cache = None
    _compiled_patterns = None


def allowed_sectors() -> set[str]:
    """Return the set of valid sector keys (used by API validation)."""
    tax = _load_taxonomy()
    return set(tax.get("allowed_sectors") or [])


def detect_sector(
    employer_name: str,
    adapter_name: str = "",
    base_url: str = "",
) -> str:
    """Return the sector key for this (employer, adapter, url) triple.

    Always returns one of the keys in ``allowed_sectors``. Defaults to
    ``unknown`` when no rule matches.

    The function is pure and fast — the YAML is cached at module level
    and patterns are pre-compiled.
    """
    tax = _load_taxonomy()
    allowed = set(tax.get("allowed_sectors") or [])

    # 1. Aggregator adapter override
    if adapter_name in _AGGREGATOR_ADAPTERS:
        return "aggregator" if "aggregator" in allowed else "unknown"

    key = _slugify(employer_name)

    # 2. Explicit employer mapping
    employers = tax.get("employers") or {}
    if key in employers:
        sector = employers[key]
        # Guard against typos in the YAML — fall back to unknown if
        # the value isn't in the allowed enum.
        return sector if sector in allowed else "unknown"

    # 3. Pattern fallback (skipping deny-list keys)
    deny = set(tax.get("deny_pattern_match") or [])
    if key not in deny and _compiled_patterns:
        for pattern, sector in _compiled_patterns:
            if pattern.search(employer_name or ""):
                return sector if sector in allowed else "unknown"

    # 4. Default
    return "unknown"
