from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import httpx
import yaml


def _load_yaml_routing(path: str | Path = "configs/legacy_routing.yaml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _normalise_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _live_csv_url(spreadsheet_id: str, sheet_name: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?"
        f"tqx=out:csv&sheet={sheet_name}"
    )


def _extract_live_routing(base: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, Any]:
    routing = dict(base)
    categories = dict(base.get("categories", {}))
    category_defaults = dict(base.get("category_defaults", {}))
    allowed_countries = list(base.get("allowed_countries", []))

    seen_categories: set[str] = set()
    sub_specialism_keywords: dict[str, dict[str, list[str]]] = {}
    seen_countries: set[str] = set()

    for raw in rows:
        row = {_normalise_header(k): str(v or "").strip() for k, v in raw.items()}
        category = row.get("category") or row.get("specialism") or row.get("group") or ""
        sub_spec = row.get("sub specialism") or row.get("sub-specialism") or row.get("subspecialism") or ""
        country = row.get("country") or ""

        if category:
            seen_categories.add(category)
            if category not in category_defaults:
                category_defaults[category] = sub_spec or category_defaults.get(category, "Other")
            sub_specialism_keywords.setdefault(category, {})
            if sub_spec:
                sub_specialism_keywords[category].setdefault(sub_spec, [sub_spec.lower()])

        if country and country not in seen_countries:
            seen_countries.add(country)
            allowed_countries.append(country)

    if seen_categories:
        # preserve existing internal taxonomy-key mapping but refresh any exact-name matches
        refreshed_categories = {}
        for internal_key, existing_label in categories.items():
            if existing_label in seen_categories:
                refreshed_categories[internal_key] = existing_label
            else:
                refreshed_categories[internal_key] = existing_label
        categories = refreshed_categories

    if seen_categories:
        routing["sub_specialism_keywords"] = sub_specialism_keywords | dict(base.get("sub_specialism_keywords", {}))
        routing["category_defaults"] = category_defaults
    if seen_countries:
        routing["allowed_countries"] = allowed_countries
    routing["categories"] = categories
    return routing


def load_legacy_routing(path: str | Path = "configs/legacy_routing.yaml") -> dict[str, Any]:
    base = _load_yaml_routing(path)
    live_source = base.get("live_source", {}) or {}
    if not live_source.get("enabled", False):
        return base

    spreadsheet_id = str(live_source.get("spreadsheet_id", "")).strip()
    sheet_name = str(live_source.get("sheet_name", "Taxonomy")).strip() or "Taxonomy"
    timeout_seconds = float(live_source.get("timeout_seconds", 10))
    if not spreadsheet_id:
        return base

    url = _live_csv_url(spreadsheet_id=spreadsheet_id, sheet_name=sheet_name)
    try:
        response = httpx.get(url, timeout=timeout_seconds, follow_redirects=True)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        if not rows:
            return base
        return _extract_live_routing(base, rows)
    except Exception:
        return base


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
