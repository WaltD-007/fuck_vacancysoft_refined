from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_legacy_routing(path: str | Path = "configs/legacy_routing.yaml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def map_category(primary_taxonomy_key: str | None, routing: dict[str, Any]) -> str:
    categories = routing.get("categories", {})
    if not primary_taxonomy_key:
        return "Other"
    return categories.get(primary_taxonomy_key, "Other")


def map_sub_specialism(title: str | None, category: str, routing: dict[str, Any]) -> str:
    title_l = (title or "").strip().lower()
    keyword_map = routing.get("sub_specialism_keywords", {}).get(category, {})
    for label, patterns in keyword_map.items():
        if any(pattern in title_l for pattern in patterns or []):
            return label
    defaults = routing.get("category_defaults", {})
    return defaults.get(category, "Other")


def normalise_country(country_value: str | None, routing: dict[str, Any]) -> str:
    value = (country_value or "").strip()
    if not value:
        return "N/A"

    allowed = set(routing.get("allowed_countries", []))
    if value in allowed:
        return value

    aliases = {str(k).lower(): str(v) for k, v in (routing.get("country_aliases", {}) or {}).items()}
    alias_value = aliases.get(value.lower())
    if alias_value in allowed:
        return alias_value
    return "N/A"
