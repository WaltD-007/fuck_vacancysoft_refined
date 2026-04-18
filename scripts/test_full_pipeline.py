#!/usr/bin/env python3
"""
Full pipeline integration test.

Runs discovery against one board per adapter, then enriches, classifies,
scores, and exports the results to an Excel file.  Capped at 100 leads.

Usage:
    python3 scripts/test_full_pipeline.py
    python3 scripts/test_full_pipeline.py --output results.xlsx
    python3 scripts/test_full_pipeline.py --skip-browser        # API-only adapters
    python3 scripts/test_full_pipeline.py --adapters workday,greenhouse
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root is importable and env vars are loaded
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

_ENV_FILES = [PROJECT_ROOT / ".env", PROJECT_ROOT / "alembic" / "env"]
for _env_path in _ENV_FILES:
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ---------------------------------------------------------------------------
# Imports (after path setup)
# ---------------------------------------------------------------------------
from sqlalchemy import func, select

from vacancysoft.adapters import (
    AdzunaAdapter,
    AshbyAdapter,
    EFinancialCareersAdapter,
    EightfoldAdapter,
    GenericBrowserAdapter,
    GoogleJobsAdapter,
    GreenhouseAdapter,
    IcimsAdapter,
    LeverAdapter,
    OracleCloudAdapter,
    ReedAdapter,
    SmartRecruitersAdapter,
    SuccessFactorsAdapter,
    WorkableAdapter,
    WorkdayAdapter,
)
from vacancysoft.adapters.base import SourceAdapter
from vacancysoft.db.base import Base
from vacancysoft.db.engine import build_engine
from vacancysoft.db.models import (
    ClassificationResult,
    EnrichedJob,
    RawJob,
    ScoreResult,
    Source,
    SourceRun,
)
from vacancysoft.db.session import SessionLocal
from vacancysoft.exporters.excel_exporter import export_profile_to_excel
from vacancysoft.pipelines.classification_persistence import classify_enriched_jobs
from vacancysoft.pipelines.enrichment_persistence import enrich_raw_jobs
from vacancysoft.pipelines.persistence import persist_discovery_batch
from vacancysoft.pipelines.scoring_persistence import score_enriched_jobs
from vacancysoft.source_registry.config_seed_loader import seed_sources_from_config

# ---------------------------------------------------------------------------
# One test source per adapter — picked for reliability
# ---------------------------------------------------------------------------
BROWSER_ADAPTERS = {"generic_site", "icims", "oracle", "successfactors", "eightfold", "efinancialcareers"}

TEST_SOURCES: list[dict] = [
    # --- API adapters ---
    {
        "name": "workday",
        "adapter": "workday",
        "company": "NatWest Group",
        "config": {
            "endpoint_url": "https://rbs.wd3.myworkdayjobs.com/wday/cxs/rbs/RBS/jobs",
            "job_board_url": "https://rbs.wd3.myworkdayjobs.com/en-US/RBS",
            "tenant": "rbs",
            "shard": "wd3",
            "site_path": "RBS",
        },
    },
    {
        "name": "greenhouse",
        "adapter": "greenhouse",
        "company": "Man Group",
        "config": {"slug": "mangroup", "job_board_url": "https://job-boards.eu.greenhouse.io/mangroup/jobs"},
    },
    {
        "name": "workable",
        "adapter": "workable",
        "company": "Hayfin",
        "config": {"slug": "hayfin-capital-management", "job_board_url": "https://apply.workable.com/hayfin-capital-management"},
    },
    {
        "name": "ashby",
        "adapter": "ashby",
        "company": "Allica Bank",
        "config": {"slug": "allica-bank", "job_board_url": "https://jobs.ashbyhq.com/allica-bank"},
    },
    {
        "name": "smartrecruiters",
        "adapter": "smartrecruiters",
        "company": "AJ Bell",
        "config": {"slug": "AJBell1", "job_board_url": "https://jobs.smartrecruiters.com/AJBell1"},
    },
    {
        "name": "lever",
        "adapter": "lever",
        "company": "Plaid",
        "config": {"slug": "plaid", "job_board_url": "https://jobs.lever.co/plaid"},
    },
    {
        "name": "adzuna",
        "adapter": "adzuna",
        "company": "Adzuna Aggregator",
        "config": {
            "search_terms": ["risk manager"],
            "countries": ["gb"],
            "max_pages": 1,
            "results_per_page": 20,
        },
    },
    {
        "name": "reed",
        "adapter": "reed",
        "company": "Reed Aggregator",
        "config": {"search_terms": ["risk manager"], "results_per_page": 20},
    },
    {
        "name": "google_jobs",
        "adapter": "google_jobs",
        "company": "Google Jobs Aggregator",
        "config": {"search_terms": ["quantitative analyst london"], "max_pages": 1},
    },
    # --- Browser adapters ---
    {
        "name": "eightfold",
        "adapter": "eightfold",
        "company": "Morgan Stanley",
        "config": {"job_board_url": "https://morganstanley.eightfold.ai/careers"},
    },
    {
        "name": "oracle",
        "adapter": "oracle",
        "company": "JPMorgan Chase",
        "config": {"job_board_url": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"},
    },
    {
        "name": "successfactors",
        "adapter": "successfactors",
        "company": "Standard Chartered",
        "config": {"job_board_url": "https://career2.successfactors.eu/career?company=standardch"},
    },
    {
        "name": "icims",
        "adapter": "icims",
        "company": "StoneX",
        "config": {"slug": "uk-stonex", "job_board_url": "https://uk-stonex.icims.com/jobs"},
    },
    {
        "name": "efinancialcareers",
        "adapter": "efinancialcareers",
        "company": "eFinancialCareers",
        "config": {},
    },
    {
        "name": "generic_browser",
        "adapter": "generic_site",
        "company": "Enstar Group",
        "config": {"job_board_url": "https://careers.enstargroup.com/search-our-jobs"},
    },
]

ADAPTER_CLASSES: dict[str, type[SourceAdapter]] = {
    "workday": WorkdayAdapter,
    "greenhouse": GreenhouseAdapter,
    "workable": WorkableAdapter,
    "ashby": AshbyAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "lever": LeverAdapter,
    "icims": IcimsAdapter,
    "oracle": OracleCloudAdapter,
    "successfactors": SuccessFactorsAdapter,
    "eightfold": EightfoldAdapter,
    "generic_site": GenericBrowserAdapter,
    "adzuna": AdzunaAdapter,
    "efinancialcareers": EFinancialCareersAdapter,
    "reed": ReedAdapter,
    "google_jobs": GoogleJobsAdapter,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
LEAD_CAP = 100

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _banner(msg: str) -> None:
    width = 60
    print(f"\n{BOLD}{CYAN}{'=' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * width}{RESET}\n")


def _step(msg: str) -> None:
    print(f"{BOLD}>>> {msg}{RESET}")


def _ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {CYAN}INFO{RESET}  {msg}")


def _ensure_source_exists(session, source_def: dict) -> Source:
    """Create or fetch a Source row for this test adapter."""
    source_key = f"test_{source_def['adapter']}_{source_def['name']}"
    existing = session.execute(
        select(Source).where(Source.source_key == source_key)
    ).scalar_one_or_none()

    if existing:
        return existing

    url = source_def["config"].get("job_board_url") or source_def["config"].get("endpoint_url") or "https://example.com"
    from urllib.parse import urlparse
    hostname = urlparse(url).hostname or "example.com"

    source = Source(
        source_key=source_key,
        employer_name=source_def["company"],
        board_name=source_def["name"],
        base_url=url,
        hostname=hostname,
        source_type="test",
        ats_family=source_def["adapter"],
        adapter_name=source_def["adapter"],
        active=True,
        seed_type="test_seed",
        discovery_method="test_script",
        fingerprint=f"{hostname}|test",
        config_blob=source_def["config"],
        capability_blob={},
    )
    session.add(source)
    session.flush()
    return source


def _check_env_for_adapter(adapter_name: str) -> str | None:
    """Return a warning message if required env vars are missing, else None."""
    if adapter_name == "adzuna":
        if not os.environ.get("ADZUNA_APP_ID") or not os.environ.get("ADZUNA_APP_KEY"):
            return "Missing ADZUNA_APP_ID / ADZUNA_APP_KEY"
    if adapter_name == "reed":
        if not os.environ.get("REED_API_KEY"):
            return "Missing REED_API_KEY"
    if adapter_name == "google_jobs":
        if not os.environ.get("SERPAPI_KEY"):
            return "Missing SERPAPI_KEY"
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Full pipeline integration test")
    parser.add_argument("--output", default=None, help="Excel output path (default: auto-generated)")
    parser.add_argument("--skip-browser", action="store_true", help="Skip browser-based adapters (faster, but still runs efinancialcareers and generic_site)")
    parser.add_argument("--adapters", default=None, help="Comma-separated adapter names to test (e.g. workday,greenhouse)")
    parser.add_argument("--api-only", action="store_true", help="Strictly API-only adapters, no browser at all")
    parser.add_argument("--cap", type=int, default=LEAD_CAP, help=f"Max leads to export (default: {LEAD_CAP})")
    args = parser.parse_args()

    output_path = args.output or f"test_pipeline_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    lead_cap = args.cap
    adapter_filter = set(args.adapters.split(",")) if args.adapters else None

    _banner("FULL PIPELINE INTEGRATION TEST")
    _info(f"Lead cap: {lead_cap}")
    _info(f"Output: {output_path}")
    _info(f"Skip browser: {args.skip_browser}")
    if adapter_filter:
        _info(f"Adapter filter: {', '.join(sorted(adapter_filter))}")

    # -----------------------------------------------------------------------
    # Step 0: Ensure DB schema exists
    # -----------------------------------------------------------------------
    _step("Step 0: Ensuring database schema")
    engine = build_engine()
    Base.metadata.create_all(bind=engine)
    _ok("Schema ready")

    # -----------------------------------------------------------------------
    # Step 1: Discovery — one board per adapter
    # -----------------------------------------------------------------------
    _banner("STEP 1: DISCOVERY")

    sources_to_test = TEST_SOURCES[:]
    if args.api_only:
        # Strictly no browser adapters
        sources_to_test = [s for s in sources_to_test if s["adapter"] not in BROWSER_ADAPTERS]
    elif args.skip_browser:
        # Skip heavy browser adapters but keep efinancialcareers and generic_site
        keep_browser = {"generic_site", "efinancialcareers"}
        sources_to_test = [s for s in sources_to_test if s["adapter"] not in BROWSER_ADAPTERS or s["adapter"] in keep_browser]
    if adapter_filter:
        sources_to_test = [s for s in sources_to_test if s["adapter"] in adapter_filter or s["name"] in adapter_filter]

    discovery_results: list[dict] = []
    total_discovered = 0

    for source_def in sources_to_test:
        adapter_name = source_def["adapter"]
        label = f"{source_def['name']} ({source_def['company']})"

        # Check env vars
        env_warning = _check_env_for_adapter(adapter_name)
        if env_warning:
            _warn(f"{label}: {env_warning} — skipping")
            discovery_results.append({"name": source_def["name"], "status": "skipped", "reason": env_warning, "jobs": 0})
            continue

        adapter_cls = ADAPTER_CLASSES.get(adapter_name)
        if not adapter_cls:
            _fail(f"{label}: no adapter class found")
            discovery_results.append({"name": source_def["name"], "status": "fail", "reason": "no adapter class", "jobs": 0})
            continue

        _step(f"Discovering: {label}")
        config = dict(source_def["config"])
        config.setdefault("company", source_def["company"])

        t0 = time.time()
        try:
            adapter_instance = adapter_cls()
            page = asyncio.run(adapter_instance.discover(source_config=config))
            elapsed = time.time() - t0
            job_count = len(page.jobs)
            total_discovered += job_count

            if job_count > 0:
                # Persist to DB
                with SessionLocal() as session:
                    source_obj = _ensure_source_exists(session, source_def)
                    _run, raw_count = persist_discovery_batch(
                        session=session, source=source_obj, records=page.jobs, trigger="test_script",
                    )
                _ok(f"{label}: {job_count} jobs discovered, {raw_count} persisted ({elapsed:.1f}s)")
                # Show a sample
                sample = page.jobs[0]
                _info(f"  Sample: {sample.title_raw} | {sample.location_raw}")
            else:
                _warn(f"{label}: 0 jobs returned ({elapsed:.1f}s)")

            discovery_results.append({"name": source_def["name"], "status": "ok", "jobs": job_count, "time": f"{elapsed:.1f}s"})

        except Exception as exc:
            elapsed = time.time() - t0
            _fail(f"{label}: {type(exc).__name__}: {exc} ({elapsed:.1f}s)")
            discovery_results.append({"name": source_def["name"], "status": "fail", "reason": str(exc), "jobs": 0})

        # No early stop — we want results from every adapter

    # -----------------------------------------------------------------------
    # Step 2: Enrichment
    # -----------------------------------------------------------------------
    _banner("STEP 2: ENRICHMENT")
    _step("Enriching raw jobs")
    t0 = time.time()
    with SessionLocal() as session:
        enriched_count = enrich_raw_jobs(session, limit=lead_cap * 2)
    _ok(f"Enriched {enriched_count} jobs ({time.time() - t0:.1f}s)")

    # -----------------------------------------------------------------------
    # Step 3: Classification
    # -----------------------------------------------------------------------
    _banner("STEP 3: CLASSIFICATION")
    _step("Classifying enriched jobs")
    t0 = time.time()
    with SessionLocal() as session:
        classified_count = classify_enriched_jobs(session, limit=lead_cap * 2)
    _ok(f"Classified {classified_count} jobs ({time.time() - t0:.1f}s)")

    # -----------------------------------------------------------------------
    # Step 4: Scoring
    # -----------------------------------------------------------------------
    _banner("STEP 4: SCORING")
    _step("Scoring classified jobs")
    t0 = time.time()
    with SessionLocal() as session:
        scored_count = score_enriched_jobs(session, limit=lead_cap * 2)
    _ok(f"Scored {scored_count} jobs ({time.time() - t0:.1f}s)")

    # -----------------------------------------------------------------------
    # Step 5: Export to Excel
    # -----------------------------------------------------------------------
    _banner("STEP 5: EXPORT")
    _step(f"Exporting up to {lead_cap} leads to {output_path}")
    t0 = time.time()
    try:
        with SessionLocal() as session:
            path = export_profile_to_excel(
                session, profile_name="accepted_plus_review", output_path=output_path, limit=lead_cap,
            )
        _ok(f"Excel written to {path} ({time.time() - t0:.1f}s)")
    except Exception as exc:
        _fail(f"Excel export failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    _banner("SUMMARY")

    with SessionLocal() as session:
        stats = {
            "sources": session.execute(select(func.count(Source.id))).scalar(),
            "source_runs": session.execute(select(func.count(SourceRun.id))).scalar(),
            "raw_jobs": session.execute(select(func.count(RawJob.id))).scalar(),
            "enriched_jobs": session.execute(select(func.count(EnrichedJob.id))).scalar(),
            "classifications": session.execute(select(func.count(ClassificationResult.id))).scalar(),
            "scores": session.execute(select(func.count(ScoreResult.id))).scalar(),
        }

    _info("Database totals:")
    for key, val in stats.items():
        _info(f"  {key}: {val}")

    print()
    _info("Discovery results per adapter:")
    ok_count = 0
    fail_count = 0
    skip_count = 0
    for r in discovery_results:
        status = r["status"]
        if status == "ok":
            ok_count += 1
            _ok(f"{r['name']}: {r['jobs']} jobs ({r.get('time', '?')})")
        elif status == "skipped":
            skip_count += 1
            _warn(f"{r['name']}: SKIPPED — {r.get('reason', '?')}")
        else:
            fail_count += 1
            _fail(f"{r['name']}: FAILED — {r.get('reason', '?')}")

    print()
    print(f"{BOLD}Adapters: {GREEN}{ok_count} ok{RESET}, {RED}{fail_count} failed{RESET}, {YELLOW}{skip_count} skipped{RESET}")
    print(f"{BOLD}Pipeline: {stats['raw_jobs']} raw → {stats['enriched_jobs']} enriched → {stats['classifications']} classified → {stats['scores']} scored{RESET}")
    print(f"{BOLD}Output: {output_path}{RESET}")
    print()


if __name__ == "__main__":
    main()
