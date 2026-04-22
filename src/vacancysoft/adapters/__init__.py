"""
Auto-discovery for adapter modules.

Drop any .py file into this package that defines a class inheriting from
``SourceAdapter`` with an ``adapter_name`` class attribute and it will be
picked up automatically — no manual imports or registry edits needed.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any

from vacancysoft.adapters.base import SourceAdapter

logger = logging.getLogger(__name__)

# Walk every .py module in this package (except base and __init__)
_PACKAGE_DIR = Path(__file__).resolve().parent
_SKIP = {"base", "__init__"}

ADAPTER_REGISTRY: dict[str, type[SourceAdapter]] = {}

for _finder, _module_name, _is_pkg in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
    if _module_name in _SKIP or _is_pkg:
        continue
    try:
        _mod = importlib.import_module(f"vacancysoft.adapters.{_module_name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to import adapter module %s: %s", _module_name, exc)
        continue

    for _attr_name in dir(_mod):
        _obj = getattr(_mod, _attr_name)
        if (
            inspect.isclass(_obj)
            and issubclass(_obj, SourceAdapter)
            and _obj is not SourceAdapter
            and hasattr(_obj, "adapter_name")
        ):
            # Class-level opt-out so a broken adapter can be kept on
            # disk (so we don't lose the code) but not wired into the
            # registry. Worker / CLI gracefully handle a missing
            # adapter — they log and skip. Set `disabled = True` as a
            # class attribute on the adapter (see phenom.py for the
            # example — 2026-04-22 audit showed it was scraping UI
            # chrome not real jobs).
            if getattr(_obj, "disabled", False):
                logger.info("Skipping disabled adapter %s", _obj.__name__)
                continue
            key = _obj.adapter_name
            if key in ADAPTER_REGISTRY:
                logger.debug(
                    "Adapter key %r already registered (%s), skipping %s",
                    key, ADAPTER_REGISTRY[key].__name__, _obj.__name__,
                )
                continue
            ADAPTER_REGISTRY[key] = _obj

# Re-export everything that was previously importable from this package so
# existing ``from vacancysoft.adapters import FooAdapter`` still works.
_all_exports: list[str] = ["ADAPTER_REGISTRY", "SourceAdapter"]
for _cls in ADAPTER_REGISTRY.values():
    globals()[_cls.__name__] = _cls
    _all_exports.append(_cls.__name__)

# Keep derive_workday_candidate_endpoints accessible if it exists
try:
    from vacancysoft.adapters.workday import derive_workday_candidate_endpoints  # noqa: F401
    _all_exports.append("derive_workday_candidate_endpoints")
except ImportError:
    pass

__all__ = _all_exports
