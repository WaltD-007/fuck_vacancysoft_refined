"""Seed Source records from the board lists defined in configs/config.py."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import Source


def _slugify(value: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


PLATFORM_REGISTRY: dict[str, dict] = {
    "workday":         {"adapter": "workday",         "source_type": "ats_api",      "ats_family": "workday",         "board_name": "Workday"},
    "greenhouse":      {"adapter": "greenhouse",      "source_type": "ats_api",      "ats_family": "greenhouse",      "board_name": "Greenhouse"},
    "workable":        {"adapter": "workable",        "source_type": "ats_api",      "ats_family": "workable",        "board_name": "Workable"},
    "ashby":           {"adapter": "ashby",           "source_type": "ats_api",      "ats_family": "ashby",           "board_name": "Ashby"},
    "smartrecruiters": {"adapter": "smartrecruiters", "source_type": "ats_api",      "ats_family": "smartrecruiters", "board_name": "SmartRecruiters"},
    "lever":           {"adapter": "lever",           "source_type": "ats_api",      "ats_family": "lever",           "board_name": "Lever"},
    "icims":           {"adapter": "icims",           "source_type": "browser_site", "ats_family": "icims",           "board_name": "iCIMS"},
    "oracle":          {"adapter": "oracle",          "source_type": "browser_site", "ats_family": "oracle",          "board_name": "Oracle Cloud"},
    "successfactors":  {"adapter": "successfactors",  "source_type": "browser_site", "ats_family": "successfactors",  "board_name": "SuccessFactors"},
    "eightfold":       {"adapter": "eightfold",       "source_type": "browser_site", "ats_family": "eightfold",       "board_name": "Eightfold"},
    "generic_browser": {"adapter": "generic_site",    "source_type": "browser_site", "ats_family": None,              "board_name": "Generic Browser"},
}


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _build_config_blob_workday(board: object) -> dict:
    return {
        "endpoint_url": board.api_url,
        "job_board_url": board.board_url,
        "tenant": board.tenant,
        "shard": board.shard,
        "site_path": board.site_path,
    }


def _build_config_blob_with_slug(board: dict) -> dict:
    return {
        "slug": board["slug"],
        "job_board_url": board["url"],
    }


def _build_config_blob_url_only(board: dict) -> dict:
    return {
        "job_board_url": board["url"],
    }


def seed_sources_from_config(session: Session) -> tuple[int, int]:
    import sys
    from pathlib import Path
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    from configs.config import (
        ASHBY_BOARDS,
        EIGHTFOLD_BOARDS,
        GENERIC_BROWSER_BOARDS,
        GREENHOUSE_BOARDS,
        ICIMS_BOARDS,
        LEVER_BOARDS,
        ORACLE_BOARDS,
        SMARTRECRUITERS_BOARDS,
        SUCCESSFACTORS_BOARDS,
        WORKABLE_BOARDS,
        WORKDAY_BOARDS,
    )

    all_boards: list[tuple[str, list]] = [
        ("workday", WORKDAY_BOARDS),
        ("oracle", ORACLE_BOARDS),
        ("greenhouse", GREENHOUSE_BOARDS),
        ("ashby", ASHBY_BOARDS),
        ("smartrecruiters", SMARTRECRUITERS_BOARDS),
        ("workable", WORKABLE_BOARDS),
        ("eightfold", EIGHTFOLD_BOARDS),
        ("successfactors", SUCCESSFACTORS_BOARDS),
        ("generic_browser", GENERIC_BROWSER_BOARDS),
        ("lever", LEVER_BOARDS),
        ("icims", ICIMS_BOARDS),
    ]

    slug_platforms = {"greenhouse", "workable", "ashby", "smartrecruiters", "lever", "icims"}

    created = 0
    updated = 0

    for platform_key, boards in all_boards:
        meta = PLATFORM_REGISTRY[platform_key]
        adapter_name = meta["adapter"]
        source_type = meta["source_type"]
        ats_family = meta["ats_family"]
        board_name = meta["board_name"]

        for board in boards:
            # Extract URL and company — Workday uses dataclass attrs, others are dicts
            if platform_key == "workday":
                base_url = board.board_url
                company = board.company
                config_blob = _build_config_blob_workday(board)
            elif platform_key in slug_platforms:
                base_url = board["url"]
                company = board["company"]
                config_blob = _build_config_blob_with_slug(board)
            else:
                base_url = board["url"]
                company = board["company"]
                config_blob = _build_config_blob_url_only(board)

            source_key = f"{adapter_name}_{_slugify(company)}_{_url_hash(base_url)}"
            parsed = urlparse(base_url)
            hostname = parsed.hostname or "unknown"
            fingerprint = f"{hostname}|{ats_family or adapter_name}"

            existing = session.execute(
                select(Source).where(Source.source_key == source_key)
            ).scalar_one_or_none()

            values = {
                "source_key": source_key,
                "employer_name": company,
                "board_name": board_name,
                "base_url": base_url,
                "hostname": hostname,
                "source_type": source_type,
                "ats_family": ats_family,
                "adapter_name": adapter_name,
                "active": True,
                "seed_type": "config_seed",
                "discovery_method": "config_py_seed",
                "fingerprint": fingerprint,
                "canonical_company_key": _slugify(company),
                "config_blob": config_blob,
                "capability_blob": {},
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
