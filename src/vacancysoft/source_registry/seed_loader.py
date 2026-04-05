from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import Source


def _slugify(value: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def load_seed_config(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def seed_sources_from_yaml(session: Session, path: str | Path) -> tuple[int, int]:
    payload = load_seed_config(path)
    created = 0
    updated = 0

    for employer in payload.get("employers", []):
        employer_name = employer["employer_name"]
        canonical_company_key = employer.get("canonical_company_key") or _slugify(employer_name)
        known_sources = employer.get("known_sources") or []

        if known_sources:
            source_specs = known_sources
        else:
            source_specs = [{
                "source_key": f"{canonical_company_key}_careers_main",
                "base_url": employer["careers_url"],
                "adapter_name": "generic_site",
                "source_type": "browser_site",
                "ats_family": None,
                "active": True,
                "capability_overrides": {
                    "supports_api": False,
                    "supports_html": True,
                    "supports_browser": True,
                    "supports_detail_fetch": True,
                },
            }]

        for spec in source_specs:
            source_key = spec["source_key"]
            existing = session.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()
            parsed = urlparse(spec["base_url"])
            fingerprint = f"{parsed.hostname or 'unknown'}|{spec.get('ats_family') or spec['adapter_name']}"
            values = {
                "source_key": source_key,
                "employer_name": employer_name,
                "board_name": spec.get("board_name"),
                "base_url": spec["base_url"],
                "hostname": parsed.hostname or "unknown",
                "source_type": spec["source_type"],
                "ats_family": spec.get("ats_family"),
                "adapter_name": spec["adapter_name"],
                "active": bool(spec.get("active", True)),
                "seed_type": "manual_seed",
                "discovery_method": "yaml_seed",
                "fingerprint": fingerprint,
                "canonical_company_key": canonical_company_key,
                "config_blob": {
                    "priority": employer.get("priority"),
                    "geography_hints": employer.get("geography_hints", []),
                    "primary_domain": employer.get("primary_domain"),
                    "careers_url": employer.get("careers_url"),
                },
                "capability_blob": spec.get("capability_overrides", {}),
            }

            if existing is None:
                session.add(Source(**values))
                created += 1
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
                updated += 1

    session.commit()
    return created, updated
