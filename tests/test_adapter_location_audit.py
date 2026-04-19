"""Static audit of location extraction across all adapters.

Walks each module under src/vacancysoft/adapters/ with ast and inspects every
DiscoveredJobRecord(...) constructor call. An adapter is classified as:

* ``scrapes``  — every constructor passes a non-``None`` expression for
                 ``location_raw`` (adapter surfaces location itself)
* ``none``     — every constructor passes the literal ``None``
                 (adapter relies on enrichment downstream)
* ``mixed``    — some constructors pass a value, others pass ``None``
                 (possible regression — some code paths drop location)
* ``no-calls`` — no DiscoveredJobRecord constructor found (unusual)

Promoted from ``scripts/audit_adapter_locations.py``. The script was a
one-shot CLI report; this pytest pins the current baseline so new commits
that silently drop ``location_raw=...`` from a working adapter fail the
suite instead of shipping.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ADAPTERS_DIR = Path(__file__).resolve().parent.parent / "src" / "vacancysoft" / "adapters"

EXCLUDE_FILENAMES = {"__init__.py", "base.py"}

# Adapters that are currently partial (some ctor calls still pass
# location_raw=None). Leaving them here is a conscious baseline; if one
# regresses, this set needs trimming deliberately — the test WILL fail if
# an adapter not in this set drops into "mixed" state.
KNOWN_MIXED: frozenset[str] = frozenset({
    "hibob",
    "icims",
    "oracle_cloud",
    "salesforce_recruit",
})


def _is_discovered_record_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "DiscoveredJobRecord":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "DiscoveredJobRecord":
        return True
    return False


def _classify(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ctor_calls = 0
    none_count = 0
    expr_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_discovered_record_call(node):
            continue
        ctor_calls += 1
        location_expr: ast.AST | None = None
        for kw in node.keywords:
            if kw.arg == "location_raw":
                location_expr = kw.value
                break
        if location_expr is None:
            # Positional form: treat as unknown/None-like.
            none_count += 1
            continue
        if isinstance(location_expr, ast.Constant) and location_expr.value is None:
            none_count += 1
            continue
        expr_count += 1
    if ctor_calls == 0:
        return "no-calls"
    if expr_count == 0:
        return "none"
    if none_count == 0:
        return "scrapes"
    return "mixed"


def _adapter_files() -> list[Path]:
    return [p for p in sorted(ADAPTERS_DIR.glob("*.py")) if p.name not in EXCLUDE_FILENAMES]


def test_adapters_directory_exists() -> None:
    assert ADAPTERS_DIR.is_dir(), f"adapters dir missing: {ADAPTERS_DIR}"
    assert _adapter_files(), "no adapter modules found — check EXCLUDE_FILENAMES"


@pytest.mark.parametrize("path", _adapter_files(), ids=lambda p: p.stem)
def test_adapter_location_status_no_regression(path: Path) -> None:
    """An adapter must not silently drop location_raw on a subset of paths.

    Allowed states: ``scrapes`` (always passes a value) or ``none`` (always
    passes None — an enricher-only adapter). ``mixed`` is only tolerated
    for adapters in ``KNOWN_MIXED``; a new arrival there is a regression.
    """
    status = _classify(path)
    assert status != "no-calls", (
        f"{path.stem}: no DiscoveredJobRecord() constructor found — "
        "adapter appears broken or uses unexpected indirection"
    )
    if status == "mixed":
        assert path.stem in KNOWN_MIXED, (
            f"{path.stem}: adapter dropped into 'mixed' state — some "
            f"DiscoveredJobRecord() calls pass location_raw=None while "
            f"others pass a value. Either fix the None paths or add "
            f"'{path.stem}' to KNOWN_MIXED with a reason."
        )
