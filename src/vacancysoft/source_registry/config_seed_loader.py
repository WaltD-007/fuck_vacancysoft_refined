"""Seed Source records from the board lists defined in configs/config.py."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import Source


def _slugify(value: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


# URL patterns that override adapter assignment regardless of config list placement.
# Maps regex on full URL → (adapter_name, platform_key).
_URL_ADAPTER_OVERRIDES: list[tuple[str, str, str]] = [
    (r"\.hibob\.com",           "hibob",          "hibob"),
    (r"\.successfactors\.",     "successfactors",  "successfactors"),
    (r"\.eightfold\.ai",       "eightfold",       "eightfold"),
    (r"\.icims\.com",          "icims",           "icims"),
    (r"\.pinpointhq\.com",     "pinpoint",        "pinpoint"),
    (r"\.teamtailor\.com",     "teamtailor",      "teamtailor"),
    (r"\.taleo\.net",          "taleo",           "taleo"),
    (r"\.lever\.co/",          "lever",           "lever"),
    (r"greenhouse\.io/",       "greenhouse",      "greenhouse"),
    (r"\.ashbyhq\.com",        "ashby",           "ashby"),
    (r"\.workable\.com",       "workable",        "workable"),
    (r"smartrecruiters\.com/", "smartrecruiters",  "smartrecruiters"),
    (r"myworkdayjobs\.com",    "workday",         "workday"),
    (r"\.oraclecloud\.com",    "oracle",          "oracle"),
    (r"selectminds\.com",      "selectminds",     "selectminds"),
    (r"silkroad\.com",         "silkroad",        "silkroad"),
]


def detect_adapter_from_url(url: str) -> tuple[str, str] | None:
    """Return (adapter_name, platform_key) if URL matches a known ATS, else None."""
    import re
    for pattern, adapter, platform_key in _URL_ADAPTER_OVERRIDES:
        if re.search(pattern, url, re.IGNORECASE):
            return adapter, platform_key
    return None


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
    "adzuna":          {"adapter": "adzuna",          "source_type": "ats_api",      "ats_family": "adzuna",          "board_name": "Adzuna"},
    "reed":            {"adapter": "reed",            "source_type": "ats_api",      "ats_family": "reed",            "board_name": "Reed"},
    "efinancialcareers": {"adapter": "efinancialcareers", "source_type": "browser_site", "ats_family": "efinancialcareers", "board_name": "eFinancialCareers"},
    "google_jobs":     {"adapter": "google_jobs",     "source_type": "ats_api",      "ats_family": "google_jobs",     "board_name": "Google Jobs"},
    "hibob":           {"adapter": "hibob",           "source_type": "browser_site", "ats_family": "hibob",           "board_name": "HiBob"},
    "selectminds":     {"adapter": "selectminds",     "source_type": "browser_site", "ats_family": "selectminds",     "board_name": "SelectMinds"},
    "silkroad":        {"adapter": "silkroad",        "source_type": "ats_api",      "ats_family": "silkroad",        "board_name": "SilkRoad OpenHire"},
    "taleo":           {"adapter": "taleo",           "source_type": "ats_api",      "ats_family": "taleo",           "board_name": "Taleo Enterprise"},
    "pinpoint":        {"adapter": "pinpoint",        "source_type": "ats_api",      "ats_family": "pinpoint",        "board_name": "Pinpoint"},
    "teamtailor":      {"adapter": "teamtailor",      "source_type": "ats_api",      "ats_family": "teamtailor",      "board_name": "Teamtailor"},
    "coresignal":      {"adapter": "coresignal",      "source_type": "ats_api",      "ats_family": "coresignal",      "board_name": "Coresignal"},
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
    blob: dict[str, Any] = {
        "job_board_url": board["url"],
    }
    # Pass through optional flags (e.g. use_firefox for Cloudflare sites)
    for key in ("use_firefox", "scroll_rounds", "max_pages", "page_timeout_ms"):
        if key in board:
            blob[key] = board[key]
    return blob


def seed_sources_from_config(session: Session) -> tuple[int, int, int]:
    """Seed Source records from configs/config.py board lists.

    Returns (created, skipped, total_seen).

    Behaviour: **create-only**. Existing rows (matched by source_key) are
    left untouched. This protects audit corrections + UI edits + admin
    tweaks from being reverted by re-running ``run.sh`` or
    ``prospero db seed-config-boards``. To deliberately update an existing
    row, use ``scripts/apply_source_corrections.py`` or the Sources UI.
    """
    import sys
    from pathlib import Path
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    from configs.config import (
        ADZUNA_BOARDS,
        ASHBY_BOARDS,
        EFINANCIALCAREERS_BOARDS,
        EIGHTFOLD_BOARDS,
        GENERIC_BROWSER_BOARDS,
        GOOGLE_JOBS_BOARDS,
        GREENHOUSE_BOARDS,
        HIBOB_BOARDS,
        ICIMS_BOARDS,
        LEVER_BOARDS,
        ORACLE_BOARDS,
        PINPOINT_BOARDS,
        REED_BOARDS,
        SELECTMINDS_BOARDS,
        SILKROAD_BOARDS,
        SMARTRECRUITERS_BOARDS,
        SUCCESSFACTORS_BOARDS,
        TALEO_BOARDS,
        WORKABLE_BOARDS,
        WORKDAY_BOARDS,
    )
    try:
        from configs.config import CORESIGNAL_BOARDS
    except ImportError:
        CORESIGNAL_BOARDS = []

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
        ("hibob", HIBOB_BOARDS),
        ("selectminds", SELECTMINDS_BOARDS),
        ("silkroad", SILKROAD_BOARDS),
        ("taleo", TALEO_BOARDS),
        ("pinpoint", PINPOINT_BOARDS),
        ("adzuna", ADZUNA_BOARDS),
        ("reed", REED_BOARDS),
        ("efinancialcareers", EFINANCIALCAREERS_BOARDS),
        ("google_jobs", GOOGLE_JOBS_BOARDS),
        ("coresignal", CORESIGNAL_BOARDS),
    ]

    slug_platforms = {"greenhouse", "workable", "ashby", "smartrecruiters", "lever", "icims"}

    created = 0
    skipped = 0
    total_seen = 0

    for platform_key, boards in all_boards:
        meta = PLATFORM_REGISTRY[platform_key]
        adapter_name = meta["adapter"]
        source_type = meta["source_type"]
        ats_family = meta["ats_family"]
        board_name = meta["board_name"]

        for board in boards:
            total_seen += 1
            # Extract URL and company — Workday uses dataclass attrs, others are dicts
            if platform_key == "workday":
                base_url = board.board_url
                company = board.company
                config_blob = _build_config_blob_workday(board)
            elif platform_key in slug_platforms:
                base_url = board["url"]
                company = board["company"]
                config_blob = _build_config_blob_with_slug(board)
            elif platform_key in ("adzuna", "reed", "efinancialcareers", "google_jobs", "coresignal"):
                base_url = board["url"]
                company = board["company"]
                # Aggregator sources carry their full config (search terms, locations, etc.)
                config_blob = {"job_board_url": board["url"]}
                for key in ("search_terms", "countries", "locations", "domains",
                            "max_pages", "max_pages_per_query", "results_per_page",
                            "max_per_term", "request_delay"):
                    if key in board:
                        config_blob[key] = board[key]
            else:
                base_url = board["url"]
                company = board["company"]
                config_blob = _build_config_blob_url_only(board)

            # Auto-detect adapter from URL if assigned to generic_browser
            if platform_key == "generic_browser":
                override = detect_adapter_from_url(base_url)
                if override:
                    real_adapter, real_platform = override
                    real_meta = PLATFORM_REGISTRY.get(real_platform, meta)
                    adapter_name = real_meta["adapter"]
                    source_type = real_meta["source_type"]
                    ats_family = real_meta["ats_family"]
                    board_name = real_meta["board_name"]

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
                # Create-only behaviour (2026-04-25): existing rows are sacred.
                # Audit corrections (scripts/apply_source_corrections.py),
                # Sources-page UI edits, and per-source admin tweaks live in
                # the DB and must NOT be reverted by re-seeding. Without this
                # guard, re-running run.sh would clobber the live adapter
                # assignments + config_blobs that the audit pipeline produced —
                # exactly what happened on 2026-04-24 (170 corrections, 77
                # silently reverted by an accidental run.sh).
                #
                # To deliberately change an existing source, use
                # apply_source_corrections.py or the Sources page UI.
                # See /Users/antonyberou/.claude/plans/db-as-source-of-truth.md
                # for the full long-term plan that retires this seed flow.
                skipped += 1

    session.commit()
    return created, skipped, total_seen
