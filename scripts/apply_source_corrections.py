#!/usr/bin/env python3
"""Apply operator-discovered corrections to seeded sources.

Some employers in ``configs/config.py`` ship with the wrong ATS or a URL
that points at a corporate career landing page rather than the real ATS.
The seed loader can't tell these cases apart from genuine generic-site
entries, so the sources get ingested wrong and fail every scrape run
until an operator investigates.

This script holds the list of corrections we've investigated (via live
probe / redetect script) and re-applies them to the DB on demand. It
matches by ``employer_name`` (case-insensitive) rather than ``id`` or
``source_key`` so the fix survives a DB reseed — fresh seed rows get
re-corrected.

### Provenance

All corrections here MUST have a Reason line noting:
  - how the correction was verified (live probe, API endpoint, etc.)
  - when it was discovered (so we can sanity-check years from now)

### Safety

  * Default is DRY-RUN — must pass ``--commit`` to write.
  * Idempotent: if the row already matches the corrected shape, the
    script is a no-op for that row (won't touch ``updated_at``).
  * Match is case-insensitive on ``employer_name`` only; duplicate
    rows for the same employer will ALL be corrected.

### Usage

    # Preview
    python3 scripts/apply_source_corrections.py

    # Commit
    python3 scripts/apply_source_corrections.py --commit

### Rollback

This script intentionally has no un-apply path — the corrections here
represent "truth" (the real ATS, verified live). Rolling back just
means reverting to known-broken state. If a correction here is wrong,
delete it from ``_CORRECTIONS`` rather than adding an unapply hook.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import func, select  # noqa: E402

from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source  # noqa: E402


# ── Corrections ─────────────────────────────────────────────────────────────
#
# Each entry has:
#   employer:       case-insensitive match against Source.employer_name
#   action:         "reclassify" or "deactivate"
#   reason:         one-liner, shown in Source.notes and the script summary
#
# Reclassify adds: adapter_name, ats_family, base_url, hostname, config_blob
# Deactivate adds: (nothing — just flips active=False)
#
# Append new entries here as you discover misclassifications.
# -----------------------------------------------------------------------------
_CORRECTIONS: list[dict] = [
    {
        "employer": "The Hartford",
        "action": "reclassify",
        "adapter_name": "workday",
        "ats_family": "workday",
        "base_url": "https://thehartford.wd5.myworkdayjobs.com/en-US/Careers_External",
        "hostname": "thehartford.wd5.myworkdayjobs.com",
        "config_blob": {
            "endpoint_url": "https://thehartford.wd5.myworkdayjobs.com/wday/cxs/thehartford/Careers_External/jobs",
            "job_board_url": "https://thehartford.wd5.myworkdayjobs.com/en-US/Careers_External",
            "tenant": "thehartford",
            "shard": "wd5",
            "site_path": "Careers_External",
        },
        "reason": (
            "Seeded with corporate-careers URL (thehartford.com/careers); "
            "real ATS is Workday at *.wd5.myworkdayjobs.com — verified 379 jobs "
            "on the live CXS endpoint (2026-04-24)."
        ),
    },
    {
        "employer": "Yieldstreet",
        "action": "deactivate",
        "reason": (
            "yieldstreet.com DNS unresolvable from scraper (2026-04-24). "
            "Reactivate and supply a valid ATS URL if DNS recovers or a real "
            "hosted ATS is discovered."
        ),
    },
    # ── Avature tenants — reclassify generic_site → avature ─────────────────
    # Discovered 2026-04-24 during the post-Step-4 audit: 8 DB-wide Avature
    # tenants were all classified as generic_site because no Avature adapter
    # existed. PR that follows this batch introduces src/vacancysoft/adapters/
    # avature.py; these corrections migrate the Source rows to it so the
    # dedicated adapter replaces the generic_browser + backfill dance.
    {
        "employer": "Ally Invest",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://ally.avature.net/careers",
        "hostname": "ally.avature.net",
        "config_blob": {
            "job_board_url": "https://ally.avature.net/careers",
            "use_firefox": False,  # responds to httpx / open Cloudflare
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    {
        "employer": "Berenberg",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://berenberg.avature.net/en_GB/careers",
        "hostname": "berenberg.avature.net",
        "config_blob": {
            "job_board_url": "https://berenberg.avature.net/en_GB/careers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    {
        "employer": "Bloomberg",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://bloomberg.avature.net/careers/SearchJobs/",
        "hostname": "bloomberg.avature.net",
        "config_blob": {
            "job_board_url": "https://bloomberg.avature.net/careers/SearchJobs/",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    {
        "employer": "Carlyle",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://carlyle.avature.net/externalcareers",
        "hostname": "carlyle.avature.net",
        "config_blob": {
            "job_board_url": "https://carlyle.avature.net/externalcareers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    {
        "employer": "Koch Industries",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://koch.avature.net/en_US/careers",
        "hostname": "koch.avature.net",
        "config_blob": {
            "job_board_url": "https://koch.avature.net/en_US/careers",
            "use_firefox": True,  # Koch IS Cloudflare-gated
            "max_pages": 5,
        },
        "reason": (
            "Avature ATS — migrate off generic_site to dedicated adapter. "
            "Koch is Cloudflare-gated so adapter uses Firefox transport. "
            "Note: source is currently inactive (deactivated 2026-04-24 as "
            "client de-prioritised); reclassify still applies in case of "
            "future reactivation."
        ),
    },
    {
        "employer": "Liberty Specialty Markets",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://libertymutual1.avature.net/LibertyCareers",
        "hostname": "libertymutual1.avature.net",
        "config_blob": {
            "job_board_url": "https://libertymutual1.avature.net/LibertyCareers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    {
        "employer": "Macquarie",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://recruitment.macquarie.com/en_US/careers",
        "hostname": "recruitment.macquarie.com",
        "config_blob": {
            "job_board_url": "https://recruitment.macquarie.com/en_US/careers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": (
            "Avature ATS (white-labelled at recruitment.macquarie.com) — "
            "migrate off generic_site to dedicated adapter (2026-04-24)."
        ),
    },
    {
        "employer": "Metro Bank",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://metrobank.avature.net/amazingcareers",
        "hostname": "metrobank.avature.net",
        "config_blob": {
            "job_board_url": "https://metrobank.avature.net/amazingcareers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": (
            "Avature ATS — migrate off generic_site to dedicated adapter. "
            "A duplicate row seeded as taleo (id=1280) was deactivated "
            "separately on 2026-04-24 — the taleo row was a seed-time "
            "misclassification, URL is Avature."
        ),
    },
    {
        "employer": "Tesco Insurance",
        "action": "reclassify",
        "adapter_name": "avature",
        "ats_family": "avature",
        "base_url": "https://tescoinsuranceandmoneyservices.avature.net/careers",
        "hostname": "tescoinsuranceandmoneyservices.avature.net",
        "config_blob": {
            "job_board_url": "https://tescoinsuranceandmoneyservices.avature.net/careers",
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Avature ATS — migrate off generic_site to dedicated adapter (2026-04-24).",
    },
    # ── ADP WorkforceNow reclassifications (from 2026-04-24 full-DB audit) ──
    # Each has a legit ADP URL with a CID query param (the company ID ADP uses
    # to identify the tenant); base_url alone is enough for the adp adapter.
    # Verified 200 + job-listing content via live probe before committing.
    {
        "employer": "MacKay Shields",
        "action": "reclassify",
        "adapter_name": "adp",
        "ats_family": "adp",
        "base_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=e36fcc0f-b0a6-4b8e-ba8b-1fe996800059&ccId=19000101_000001&type=JS&lang=en_US",
        "hostname": "workforcenow.adp.com",
        "config_blob": {
            "job_board_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=e36fcc0f-b0a6-4b8e-ba8b-1fe996800059&ccId=19000101_000001&type=JS&lang=en_US",
        },
        "reason": "ADP WorkforceNow — generic_site → adp via upstream URL pattern (2026-04-24 full-DB audit).",
    },
    {
        "employer": "Daiwa Capital Markets",
        "action": "reclassify",
        "adapter_name": "adp",
        "ats_family": "adp",
        "base_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=d72c1443-0fed-4e79-b4e8-0ba4b95f5a7a&ccId=19000101_000001&lang=en_US",
        "hostname": "workforcenow.adp.com",
        "config_blob": {
            "job_board_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=d72c1443-0fed-4e79-b4e8-0ba4b95f5a7a&ccId=19000101_000001&lang=en_US",
        },
        "reason": "ADP WorkforceNow — generic_site → adp via upstream URL pattern (2026-04-24 full-DB audit).",
    },
    {
        "employer": "Gowling WLG",
        "action": "reclassify",
        "adapter_name": "adp",
        "ats_family": "adp",
        "base_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=44e55e56-7ae1-4f8f-a30a-ce7af37c1ae6&ccId=1224285855_693&type=JS&lang=en_CA",
        "hostname": "workforcenow.adp.com",
        "config_blob": {
            "job_board_url": "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=44e55e56-7ae1-4f8f-a30a-ce7af37c1ae6&ccId=1224285855_693&type=JS&lang=en_CA",
        },
        "reason": "ADP WorkforceNow — generic_site → adp via upstream URL pattern (2026-04-24 full-DB audit).",
    },
    {
        "employer": "Zempler Bank",
        "action": "reclassify",
        "adapter_name": "adp",
        "ats_family": "adp",
        "base_url": "https://zemplerbank.careers.adp.com/",
        "hostname": "zemplerbank.careers.adp.com",
        "config_blob": {
            "job_board_url": "https://zemplerbank.careers.adp.com/",
        },
        "reason": "ADP Careers — generic_site → adp via upstream URL pattern (2026-04-24 full-DB audit).",
    },
    # ── Truly-dead sources from the 2026-04-24 audit ────────────────────────
    # These all returned DNS nonexistent or malformed base_url — no server to
    # reach, no fix possible without a new URL. Deactivated via the corrections
    # script so a reseed re-applies the deactivation cleanly.
    {
        "employer": "Arthur J. Gallagher",
        "action": "deactivate",
        "reason": (
            "ajg.referrals.selectminds.com DNS unresolvable from scraper "
            "(2026-04-24 audit). Reactivate with a valid ATS URL if the "
            "referrals subdomain comes back."
        ),
    },
    {
        "employer": "Havin Bank",
        "action": "deactivate",
        "reason": (
            "DNS for their careers host did not resolve from scraper "
            "(2026-04-24 audit). Reactivate once a valid URL is known."
        ),
    },
    {
        "employer": "Marken",
        "action": "deactivate",
        "reason": (
            "Source base_url is missing an http(s):// scheme (malformed). "
            "Deactivated 2026-04-24; reactivate after supplying a valid URL."
        ),
    },
    {
        "employer": "Merali Beedle",
        "action": "deactivate",
        "reason": (
            "Source base_url is missing an http(s):// scheme (malformed). "
            "Deactivated 2026-04-24; reactivate after supplying a valid URL."
        ),
    },
    # ── Auto-apply batch 1 (Workday) — audit 2026-04-24 ──────────────
    {
        "employer": 'Abcam',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://danaher.wd1.myworkdayjobs.com/DanaherJobs/job/Waltham-Massachusetts-United-States/Senior-Product-Manager--Oncology-Research-Solutions_R1300288/apply',
        "hostname": 'danaher.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://danaher.wd1.myworkdayjobs.com/wday/cxs/danaher/DanaherJobs/jobs',
            "job_board_url": 'https://danaher.wd1.myworkdayjobs.com/en-US/DanaherJobs',
            "tenant": 'danaher',
            "shard": 'wd1',
            "site_path": 'DanaherJobs',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#1001).",
    },
    {
        "employer": 'Allstate',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://allstate.wd5.myworkdayjobs.com/allstate_careers/login',
        "hostname": 'allstate.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://allstate.wd5.myworkdayjobs.com/wday/cxs/allstate/allstate_careers/jobs',
            "job_board_url": 'https://allstate.wd5.myworkdayjobs.com/en-US/allstate_careers',
            "tenant": 'allstate',
            "shard": 'wd5',
            "site_path": 'allstate_careers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#296).",
    },
    {
        "employer": 'BMO',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://bmo.wd3.myworkdayjobs.com/External/job/Toronto-ON-CAN/Vice-President--Software-Engineer--C--_R260001187/apply',
        "hostname": 'bmo.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://bmo.wd3.myworkdayjobs.com/wday/cxs/bmo/External/jobs',
            "job_board_url": 'https://bmo.wd3.myworkdayjobs.com/en-US/External',
            "tenant": 'bmo',
            "shard": 'wd3',
            "site_path": 'External',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#339).",
    },
    {
        "employer": 'Bank of America',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us/login',
        "hostname": 'ghr.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://ghr.wd1.myworkdayjobs.com/wday/cxs/ghr/lateral-us/jobs',
            "job_board_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us',
            "tenant": 'ghr',
            "shard": 'wd1',
            "site_path": 'lateral-us',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#322).",
    },
    {
        "employer": 'Bright Horizons',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://brighthorizons.wd5.myworkdayjobs.com/External-NorthAmerica/job/Bentonville-Arkansas-72713/Child-Care-Substitute--Part-Time--Bentonville-AR_JR-134033/apply',
        "hostname": 'brighthorizons.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://brighthorizons.wd5.myworkdayjobs.com/wday/cxs/brighthorizons/External-NorthAmerica/jobs',
            "job_board_url": 'https://brighthorizons.wd5.myworkdayjobs.com/en-US/External-NorthAmerica',
            "tenant": 'brighthorizons',
            "shard": 'wd5',
            "site_path": 'External-NorthAmerica',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#833).",
    },
    {
        "employer": 'Bupa',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://bupa.wd3.myworkdayjobs.com/BGUK_Event_Landing/page/be4bdfeb73bb1001da6b0d18296d0001?_gl=1*1ahzowc*_gcl_aw*R0NMLjE3NTIwNzEwNTguQ2p3S0NBandwcmpEQmhCVEVpd0ExbTFkMHFZOFhmblJsLUZaWUJUV2d6WU1pamJPbkx5OWZ3V3BKQkpRZEtxRko4UzB2MmI2UU9GTW1Cb0NqdFVRQXZEX0J3RQ..*_gcl_dc*R0NMLjE3NTIwNzEwNTguQ2p3S0NBandwcmpEQmhCVEVpd0ExbTFkMHFZOFhmblJsLUZaWUJUV2d6WU1pamJPbkx5OWZ3V3BKQkpRZEtxRko4UzB2MmI2UU9GTW1Cb0NqdFVRQXZEX0J3RQ..*_gcl_au*ODM3NjIxODI0LjE3NTc1MTY1MzI.*_ga*NzAyNTcxNDAwLjE3NDE4Nzg2ODY.*_ga_3H6QLL2SVV*czE3NTk4MjU3NjgkbzMyMCRnMSR0MTc1OTgyNjQ5NyRqMzUkbDAkaDA.',
        "hostname": 'bupa.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://bupa.wd3.myworkdayjobs.com/wday/cxs/bupa/BGUK_Event_Landing/jobs',
            "job_board_url": 'https://bupa.wd3.myworkdayjobs.com/en-US/BGUK_Event_Landing',
            "tenant": 'bupa',
            "shard": 'wd3',
            "site_path": 'BGUK_Event_Landing',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#1091).",
    },
    {
        "employer": 'Capital One',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://capitalone.wd12.myworkdayjobs.com/en-Uk/Capital_One/login',
        "hostname": 'capitalone.wd12.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://capitalone.wd12.myworkdayjobs.com/wday/cxs/capitalone/Capital_One/jobs',
            "job_board_url": 'https://capitalone.wd12.myworkdayjobs.com/en-Uk/Capital_One',
            "tenant": 'capitalone',
            "shard": 'wd12',
            "site_path": 'Capital_One',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#358).",
    },
    {
        "employer": 'Cboe Global Markets',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://cboe.wd1.myworkdayjobs.com/External_Career_CBOE/job/Singapore/Director-of-Market-Data-Sales---APAC_R-4347/apply',
        "hostname": 'cboe.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://cboe.wd1.myworkdayjobs.com/wday/cxs/cboe/External_Career_CBOE/jobs',
            "job_board_url": 'https://cboe.wd1.myworkdayjobs.com/en-US/External_Career_CBOE',
            "tenant": 'cboe',
            "shard": 'wd1',
            "site_path": 'External_Career_CBOE',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#363).",
    },
    {
        "employer": 'Cerberus',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://redriver.wd5.myworkdayjobs.com/redrivercareers/job/Chantilly-Office/Service-Delivery-Manager_REQ-3363',
        "hostname": 'redriver.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://redriver.wd5.myworkdayjobs.com/wday/cxs/redriver/redrivercareers/jobs',
            "job_board_url": 'https://redriver.wd5.myworkdayjobs.com/en-US/redrivercareers',
            "tenant": 'redriver',
            "shard": 'wd5',
            "site_path": 'redrivercareers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#365).",
    },
    {
        "employer": 'Chanel UK',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://cc.wd3.myworkdayjobs.com/en-US/ChanelCareers',
        "hostname": 'cc.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://cc.wd3.myworkdayjobs.com/wday/cxs/cc/ChanelCareers/jobs',
            "job_board_url": 'https://cc.wd3.myworkdayjobs.com/en-US/ChanelCareers',
            "tenant": 'cc',
            "shard": 'wd3',
            "site_path": 'ChanelCareers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#838).",
    },
    {
        "employer": 'FIS',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://fis.wd5.myworkdayjobs.com/SearchJobs/job/GBR-LNDN-25-Walbrook-FL56/Business-Development-Representative---German-Speaking_JR0303368/apply',
        "hostname": 'fis.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://fis.wd5.myworkdayjobs.com/wday/cxs/fis/SearchJobs/jobs',
            "job_board_url": 'https://fis.wd5.myworkdayjobs.com/en-US/SearchJobs',
            "tenant": 'fis',
            "shard": 'wd5',
            "site_path": 'SearchJobs',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#414).",
    },
    {
        "employer": 'Fiserv',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://fiserv.wd5.myworkdayjobs.com/EXT/job/Remote-New-York/Sales-Rep-BC---NYC--NY_R-10389864/apply',
        "hostname": 'fiserv.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://fiserv.wd5.myworkdayjobs.com/wday/cxs/fiserv/EXT/jobs',
            "job_board_url": 'https://fiserv.wd5.myworkdayjobs.com/en-US/EXT',
            "tenant": 'fiserv',
            "shard": 'wd5',
            "site_path": 'EXT',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#415).",
    },
    {
        "employer": 'Herbert Smith Freehills',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://herbertsmithfreehills.wd3.myworkdayjobs.com/External/job/London/Head-of-Conflicts_R-102660-1/apply',
        "hostname": 'herbertsmithfreehills.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://herbertsmithfreehills.wd3.myworkdayjobs.com/wday/cxs/herbertsmithfreehills/External/jobs',
            "job_board_url": 'https://herbertsmithfreehills.wd3.myworkdayjobs.com/en-US/External',
            "tenant": 'herbertsmithfreehills',
            "shard": 'wd3',
            "site_path": 'External',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#873).",
    },
    {
        "employer": 'Markel International Insurance Company Limited',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://markelcorp.wd5.myworkdayjobs.com/GlobalCareers',
        "hostname": 'markelcorp.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://markelcorp.wd5.myworkdayjobs.com/wday/cxs/markelcorp/GlobalCareers/jobs',
            "job_board_url": 'https://markelcorp.wd5.myworkdayjobs.com/en-US/GlobalCareers',
            "tenant": 'markelcorp',
            "shard": 'wd5',
            "site_path": 'GlobalCareers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#475).",
    },
    {
        "employer": 'Merrill',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us/login',
        "hostname": 'ghr.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://ghr.wd1.myworkdayjobs.com/wday/cxs/ghr/lateral-us/jobs',
            "job_board_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us',
            "tenant": 'ghr',
            "shard": 'wd1',
            "site_path": 'lateral-us',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#486).",
    },
    {
        "employer": 'Merrill Lynch International',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us/login',
        "hostname": 'ghr.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://ghr.wd1.myworkdayjobs.com/wday/cxs/ghr/lateral-us/jobs',
            "job_board_url": 'https://ghr.wd1.myworkdayjobs.com/en-us/lateral-us',
            "tenant": 'ghr',
            "shard": 'wd1',
            "site_path": 'lateral-us',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#487).",
    },
    {
        "employer": 'Mizuho',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://mizuho.wd1.myworkdayjobs.com/mizuhoamericas',
        "hostname": 'mizuho.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://mizuho.wd1.myworkdayjobs.com/wday/cxs/mizuho/mizuhoamericas/jobs',
            "job_board_url": 'https://mizuho.wd1.myworkdayjobs.com/en-US/mizuhoamericas',
            "tenant": 'mizuho',
            "shard": 'wd1',
            "site_path": 'mizuhoamericas',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#494).",
    },
    {
        "employer": 'OCBC',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://ocbc.wd102.myworkdayjobs.com/External',
        "hostname": 'ocbc.wd102.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://ocbc.wd102.myworkdayjobs.com/wday/cxs/ocbc/External/jobs',
            "job_board_url": 'https://ocbc.wd102.myworkdayjobs.com/en-US/External',
            "tenant": 'ocbc',
            "shard": 'wd102',
            "site_path": 'External',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#771).",
    },
    {
        "employer": 'PineBridge',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://pinebridge.wd5.myworkdayjobs.com/PineBridge_Career_Site',
        "hostname": 'pinebridge.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://pinebridge.wd5.myworkdayjobs.com/wday/cxs/pinebridge/PineBridge_Career_Site/jobs',
            "job_board_url": 'https://pinebridge.wd5.myworkdayjobs.com/en-US/PineBridge_Career_Site',
            "tenant": 'pinebridge',
            "shard": 'wd5',
            "site_path": 'PineBridge_Career_Site',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#550).",
    },
    {
        "employer": 'RBC BlueBay Asset Management',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1/',
        "hostname": 'rbc.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://rbc.wd3.myworkdayjobs.com/wday/cxs/rbc/RBCGLOBAL1/jobs',
            "job_board_url": 'https://rbc.wd3.myworkdayjobs.com/en-US/RBCGLOBAL1',
            "tenant": 'rbc',
            "shard": 'wd3',
            "site_path": 'RBCGLOBAL1',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#1086).",
    },
    {
        "employer": 'RSM International',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://rsm.wd1.myworkdayjobs.com/RSMCareers/login',
        "hostname": 'rsm.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://rsm.wd1.myworkdayjobs.com/wday/cxs/rsm/RSMCareers/jobs',
            "job_board_url": 'https://rsm.wd1.myworkdayjobs.com/en-US/RSMCareers',
            "tenant": 'rsm',
            "shard": 'wd1',
            "site_path": 'RSMCareers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#989).",
    },
    {
        "employer": 'Reinsurance Group of America (RGA)',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://rgare.wd1.myworkdayjobs.com/Careers/job/United-States-Chesterfield-MO-RGA-HQ/VP--AI---Emerging-Analytics_J25617/apply',
        "hostname": 'rgare.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://rgare.wd1.myworkdayjobs.com/wday/cxs/rgare/Careers/jobs',
            "job_board_url": 'https://rgare.wd1.myworkdayjobs.com/en-US/Careers',
            "tenant": 'rgare',
            "shard": 'wd1',
            "site_path": 'Careers',
        },
        "reason": "Source audit 2026-04-24: phenom → workday via html-workday-iframe (src#1521).",
    },
    {
        "employer": 'S&P Global',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://spgi.wd5.myworkdayjobs.com/SPGI_Careers',
        "hostname": 'spgi.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://spgi.wd5.myworkdayjobs.com/wday/cxs/spgi/SPGI_Careers/jobs',
            "job_board_url": 'https://spgi.wd5.myworkdayjobs.com/en-US/SPGI_Careers',
            "tenant": 'spgi',
            "shard": 'wd5',
            "site_path": 'SPGI_Careers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#578).",
    },
    {
        "employer": 'Shell',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://shell.wd3.myworkdayjobs.com/ShellCareers',
        "hostname": 'shell.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://shell.wd3.myworkdayjobs.com/wday/cxs/shell/ShellCareers/jobs',
            "job_board_url": 'https://shell.wd3.myworkdayjobs.com/en-US/ShellCareers',
            "tenant": 'shell',
            "shard": 'wd3',
            "site_path": 'ShellCareers',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#593).",
    },
    {
        "employer": 'TIAA',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://tiaa.wd1.myworkdayjobs.com/Search/job/Chicago-IL-USA/AVP--Capital-Markets-ETF-Specialist_R260400223-1',
        "hostname": 'tiaa.wd1.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://tiaa.wd1.myworkdayjobs.com/wday/cxs/tiaa/Search/jobs',
            "job_board_url": 'https://tiaa.wd1.myworkdayjobs.com/en-US/Search',
            "tenant": 'tiaa',
            "shard": 'wd1',
            "site_path": 'Search',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#1078).",
    },
    {
        "employer": 'Trafigura',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://trafigura.wd3.myworkdayjobs.com/TrafiguraCareerSite',
        "hostname": 'trafigura.wd3.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://trafigura.wd3.myworkdayjobs.com/wday/cxs/trafigura/TrafiguraCareerSite/jobs',
            "job_board_url": 'https://trafigura.wd3.myworkdayjobs.com/en-US/TrafiguraCareerSite',
            "tenant": 'trafigura',
            "shard": 'wd3',
            "site_path": 'TrafiguraCareerSite',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#651).",
    },
    {
        "employer": 'WBD',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://warnerbros.wd5.myworkdayjobs.com/global/job/Hyderabad-Office-Level-3--4-Block-A---East-Wing/Analyst--SAP-IP-Management_R000093408/apply',
        "hostname": 'warnerbros.wd5.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://warnerbros.wd5.myworkdayjobs.com/wday/cxs/warnerbros/global/jobs',
            "job_board_url": 'https://warnerbros.wd5.myworkdayjobs.com/en-US/global',
            "tenant": 'warnerbros',
            "shard": 'wd5',
            "site_path": 'global',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#946).",
    },
    {
        "employer": 'Webster Bank',
        "action": "reclassify",
        "adapter_name": 'workday',
        "ats_family": 'workday',
        "base_url": 'https://websteronline.wd12.myworkdayjobs.com/en-US/WebsterExternalCareerSite/introduceYourself',
        "hostname": 'websteronline.wd12.myworkdayjobs.com',
        "config_blob": {
            "endpoint_url": 'https://websteronline.wd12.myworkdayjobs.com/wday/cxs/websteronline/WebsterExternalCareerSite/jobs',
            "job_board_url": 'https://websteronline.wd12.myworkdayjobs.com/en-US/WebsterExternalCareerSite',
            "tenant": 'websteronline',
            "shard": 'wd12',
            "site_path": 'WebsterExternalCareerSite',
        },
        "reason": "Source audit 2026-04-24: generic_site → workday via html-workday-iframe (src#681).",
    },
    # ── Auto-apply batch 2 (non-Workday) — audit 2026-04-24 ──────────
    {
        "employer": 'A W Rostamani',
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://iacpey.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1',
        "hostname": 'iacpey.fa.ocs.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://iacpey.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#994).",
    },
    {
        "employer": 'AXA',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://move-en-axa.icims.com/jobs/login?loginOnly=1',
        "hostname": 'move-en-axa.icims.com',
        "config_blob": {
            "job_board_url": 'https://move-en-axa.icims.com/jobs/login?loginOnly=1',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#316).",
    },
    {
        "employer": 'AXA XL',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://move-en-axa.icims.com/jobs/login?loginOnly=1',
        "hostname": 'move-en-axa.icims.com',
        "config_blob": {
            "job_board_url": 'https://move-en-axa.icims.com/jobs/login?loginOnly=1',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#1090).",
    },
    {
        "employer": 'Abu Dhabi Investment Council',
        "action": "reclassify",
        "adapter_name": 'workable',
        "ats_family": 'workable',
        "base_url": 'https://apply.workable.com/abu-dhabi-investment-council/',
        "hostname": 'apply.workable.com',
        "config_blob": {
            "slug": 'abu-dhabi-investment-council',
            "job_board_url": 'https://apply.workable.com/abu-dhabi-investment-council',
        },
        "reason": "Source audit 2026-04-24: generic_site → workable via html-workable (src#1011).",
    },
    {
        "employer": 'Ahli United Bank',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#1038).",
    },
    {
        "employer": 'Akzo Nobel Decorative Coatings Ltd',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu/career?site=&company=akzonobelsP2&lang=en%5FUS&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu/career?site=&company=akzonobelsP2&lang=en%5FUS&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#1047).",
    },
    {
        "employer": 'Al Futtaim Carillion',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu/career?site=&company=C0001144036P&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu/career?site=&company=C0001144036P&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#1048).",
    },
    {
        "employer": 'Alcatel-Lucent',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#1053).",
    },
    {
        "employer": 'Alghanim Industries',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#816).",
    },
    {
        "employer": 'Allied Irish Banks',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#295).",
    },
    {
        "employer": 'Aon',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://aon.icims.com/icims2/servlet/icims2?module=AppInert&action=download&id=1537062&hashed=2111374838',
        "hostname": 'aon.icims.com',
        "config_blob": {
            "job_board_url": 'https://aon.icims.com/icims2/servlet/icims2?module=AppInert&action=download&id=1537062&hashed=2111374838',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#304).",
    },
    {
        "employer": 'Aspen Insurance',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#309).",
    },
    {
        "employer": 'BBC (temp)',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://api2.successfactors.eu',
        "hostname": 'api2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://api2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#824).",
    },
    {
        "employer": 'BRE Group',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://bre-1729757562.teamtailor.com/pages/benefits',
        "hostname": 'bre-1729757562.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://bre-1729757562.teamtailor.com/pages/benefits',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#825).",
    },
    {
        "employer": 'Benefact Group',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/xfpqKcBF8Ew@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/xfpqKcBF8Ew@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#329).",
    },
    {
        "employer": 'Bunge',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://performancemanager5.successfactors.eu/Bunge/Bunge_DPN_Job_Applicants.pdf',
        "hostname": 'performancemanager5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://performancemanager5.successfactors.eu/Bunge/Bunge_DPN_Job_Applicants.pdf',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#353).",
    },
    {
        "employer": 'CAIS',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://job-boards.greenhouse.io/cais/jobs/8377538002',
        "hostname": 'job-boards.greenhouse.io',
        "config_blob": {
            "slug": 'cais',
            "job_board_url": 'https://boards.greenhouse.io/cais',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#354).",
    },
    {
        "employer": 'CLS Group',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/clsgroup',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'clsgroup',
            "job_board_url": 'https://boards.greenhouse.io/clsgroup',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#376).",
    },
    {
        "employer": 'COFCO International',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#378).",
    },
    {
        "employer": 'Cargill',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu/career?career_company=cargill&lang=en_US&company=cargill&site=&loginFlowRequired=true&_s.crb=7GQWlQ1F2u73RR9o5QRvhyr14wcN%2fenWYFFu1Vh3r60%3d',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu/career?career_company=cargill&lang=en_US&company=cargill&site=&loginFlowRequired=true&_s.crb=7GQWlQ1F2u73RR9o5QRvhyr14wcN%2fenWYFFu1Vh3r60%3d',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#360).",
    },
    {
        "employer": 'Cashplus',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/Slw6YZ-1nys@eu/dashboard?locale=fr',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/Slw6YZ-1nys@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#1096).",
    },
    {
        "employer": 'Charles Schwab',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://career-schwab.icims.com/connect?back=intro&findajob=1&hashed=-626009902&mobile=false&width=1248&height=500&bga=true&needsRedirect=false&jan1offset=-420&jun1offset=-420',
        "hostname": 'career-schwab.icims.com',
        "config_blob": {
            "job_board_url": 'https://career-schwab.icims.com/connect?back=intro&findajob=1&hashed=-626009902&mobile=false&width=1248&height=500&bga=true&needsRedirect=false&jan1offset=-420&jun1offset=-420',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#368).",
    },
    {
        "employer": 'Citigroup',
        "action": "reclassify",
        "adapter_name": 'eightfold',
        "ats_family": 'eightfold',
        "base_url": 'https://citi.eightfold.ai/careers/join?jtn_form_id=jtn-early-career',
        "hostname": 'citi.eightfold.ai',
        "config_blob": {
            "job_board_url": 'https://citi.eightfold.ai/careers/join?jtn_form_id=jtn-early-career',
        },
        "reason": "Source audit 2026-04-24: generic_site → eightfold via html-eightfold (src#373).",
    },
    {
        "employer": 'Citizens',
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://hcgn.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/join-talent-community',
        "hostname": 'hcgn.fa.us2.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://hcgn.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/join-talent-community',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#374).",
    },
    {
        "employer": 'Coats Group',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#841).",
    },
    {
        "employer": 'Davidson Kempner',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://job-boards.greenhouse.io/1456754456yhgbhfg/jobs/6757249002?gh_src=03e8b4102us',
        "hostname": 'job-boards.greenhouse.io',
        "config_blob": {
            "slug": '1456754456yhgbhfg',
            "job_board_url": 'https://boards.greenhouse.io/1456754456yhgbhfg',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#391).",
    },
    {
        "employer": 'EBRD',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#397).",
    },
    {
        "employer": 'EY (temp)',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#969).",
    },
    {
        "employer": 'Ellington',
        "action": "reclassify",
        "adapter_name": 'taleo',
        "ats_family": 'taleo',
        "base_url": 'https://phh.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?org=ELLINGTONMGMTGRP&act=redirectCwsV2&cws=37',
        "hostname": 'phh.tbe.taleo.net',
        "config_blob": {
            "job_board_url": 'https://phh.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?org=ELLINGTONMGMTGRP&act=redirectCwsV2&cws=37',
        },
        "reason": "Source audit 2026-04-24: generic_site → taleo via html-taleo (src#403).",
    },
    {
        "employer": 'ExxonMobil',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career4.successfactors.com',
        "hostname": 'career4.successfactors.com',
        "config_blob": {
            "job_board_url": 'https://career4.successfactors.com',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#408).",
    },
    {
        "employer": 'Fisher Investments',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://jobs-fishercareers.icims.com/jobs/login?loginOnly=1&redirect=&in_iframe=1&hashed=124493911',
        "hostname": 'jobs-fishercareers.icims.com',
        "config_blob": {
            "job_board_url": 'https://jobs-fishercareers.icims.com/jobs/login?loginOnly=1&redirect=&in_iframe=1&hashed=124493911',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#1083).",
    },
    {
        "employer": 'GIC',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career10.successfactors.com/careers?site=&company=gicprivate&clientId=jobs2web&lang=en_GB&navBarLevel=MY_PROFILE',
        "hostname": 'career10.successfactors.com',
        "config_blob": {
            "job_board_url": 'https://career10.successfactors.com/careers?site=&company=gicprivate&clientId=jobs2web&lang=en_GB&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#421).",
    },
    {
        "employer": 'GKN',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu/careers?company=gknaerospa',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu/careers?company=gknaerospa',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#864).",
    },
    {
        "employer": 'IPL Schoeller',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://api2.successfactors.eu',
        "hostname": 'api2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://api2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#879).",
    },
    {
        "employer": 'Inchcape',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu/career?site=&company=inchcape&lang=en_GB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu/career?site=&company=inchcape&lang=en_GB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#880).",
    },
    {
        "employer": 'Insight Partners',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/nymbusinc/jobs/5980458004?utm_source=Insight+Partners+job+board&utm_medium=getro.com&gh_src=Insight+Partners+job+board',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'nymbusinc',
            "job_board_url": 'https://boards.greenhouse.io/nymbusinc',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#440).",
    },
    {
        "employer": 'Instinet',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career4.successfactors.com',
        "hostname": 'career4.successfactors.com',
        "config_blob": {
            "job_board_url": 'https://career4.successfactors.com',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#1101).",
    },
    {
        "employer": 'Intesa Sanpaolo',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#441).",
    },
    {
        "employer": 'Irish Life Group',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#444).",
    },
    {
        "employer": 'Isio',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/BNLysC24S9M@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/BNLysC24S9M@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#445).",
    },
    {
        "employer": 'Janus Henderson',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career8.successfactors.com',
        "hostname": 'career8.successfactors.com',
        "config_blob": {
            "job_board_url": 'https://career8.successfactors.com',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#447).",
    },
    {
        "employer": 'Lancashire Group',
        "action": "reclassify",
        "adapter_name": 'pinpoint',
        "ats_family": 'pinpoint',
        "base_url": 'https://lancashire-group.pinpointhq.com/vacancies',
        "hostname": 'lancashire-group.pinpointhq.com',
        "config_blob": {
            "job_board_url": 'https://lancashire-group.pinpointhq.com/vacancies',
        },
        "reason": "Source audit 2026-04-24: generic_site → pinpoint via html-pinpoint (src#454).",
    },
    {
        "employer": 'M&S (Marks & Spencers)',
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://fa-eqid-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/my-profile/sign-in',
        "hostname": 'fa-eqid-saasfaprod1.fa.ocs.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://fa-eqid-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/my-profile/sign-in',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#885).",
    },
    {
        "employer": 'MSCI',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://ukcareers-msci.icims.com',
        "hostname": 'ukcareers-msci.icims.com',
        "config_blob": {
            "job_board_url": 'https://ukcareers-msci.icims.com',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#504).",
    },
    {
        "employer": 'Mako Group',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/mako',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'mako',
            "job_board_url": 'https://boards.greenhouse.io/mako',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#473).",
    },
    {
        "employer": 'MetLife',
        "action": "reclassify",
        "adapter_name": 'avature',
        "ats_family": 'avature',
        "base_url": 'https://metlife.avature.net/ml/Culture',
        "hostname": 'metlife.avature.net',
        "config_blob": {
            "job_board_url": 'https://metlife.avature.net/ml/Culture',
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Source audit 2026-04-24: generic_site → avature via html-avature (src#488).",
    },
    {
        "employer": 'Metropolitan Commercial Bank',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://job-boards.greenhouse.io/metropolitancommercialbank',
        "hostname": 'job-boards.greenhouse.io',
        "config_blob": {
            "slug": 'metropolitancommercialbank',
            "job_board_url": 'https://boards.greenhouse.io/metropolitancommercialbank',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#490).",
    },
    {
        "employer": 'Pagaya',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/pagaya',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'pagaya',
            "job_board_url": 'https://boards.greenhouse.io/pagaya',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#534).",
    },
    {
        "employer": 'Partners Group',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu/career?career_company=PartnersGroup&site=&loginFlowRequired=true',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu/career?career_company=PartnersGroup&site=&loginFlowRequired=true',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#536).",
    },
    {
        "employer": 'Principal Asset Management',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://internalspanish-principal.icims.com/jobs/login?loginOnly=1&redirect=&in_iframe=1&sso_connection=hs-11078-saml',
        "hostname": 'internalspanish-principal.icims.com',
        "config_blob": {
            "job_board_url": 'https://internalspanish-principal.icims.com/jobs/login?loginOnly=1&redirect=&in_iframe=1&sso_connection=hs-11078-saml',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#557).",
    },
    {
        "employer": 'QinetiQ',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#903).",
    },
    {
        "employer": 'Quintet',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#561).",
    },
    {
        "employer": 'Reed & Mackay',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/reedmackay',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'reedmackay',
            "job_board_url": 'https://boards.greenhouse.io/reedmackay',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#909).",
    },
    {
        "employer": 'SEFE',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://www.successfactors.com/',
        "hostname": 'www.successfactors.com',
        "config_blob": {
            "job_board_url": 'https://www.successfactors.com/',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#588).",
    },
    {
        "employer": 'SMBC',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu/career?site=&company=smbcP&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu/career?site=&company=smbcP&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#600).",
    },
    {
        "employer": "Sainsbury's",
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://hdhe.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1?utm_medium=third+party&utm_source=SainsburysJobs',
        "hostname": 'hdhe.fa.em3.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://hdhe.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1?utm_medium=third+party&utm_source=SainsburysJobs',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#913).",
    },
    {
        "employer": "Sainsbury's Bank",
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://hdhe.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1?utm_medium=third+party&utm_source=SainsburysJobs',
        "hostname": 'hdhe.fa.em3.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://hdhe.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1?utm_medium=third+party&utm_source=SainsburysJobs',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#778).",
    },
    {
        "employer": 'Savills',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/9OxPwH4PkW0@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/9OxPwH4PkW0@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#915).",
    },
    {
        "employer": 'Savills Investment Management (UK)',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/eKM3fjf5ff8@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/eKM3fjf5ff8@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#1508).",
    },
    {
        "employer": 'Schonfeld',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://job-boards.greenhouse.io/schonfeld/jobs/7381166',
        "hostname": 'job-boards.greenhouse.io',
        "config_blob": {
            "slug": 'schonfeld',
            "job_board_url": 'https://boards.greenhouse.io/schonfeld',
        },
        "reason": "Source audit 2026-04-24: icims → greenhouse via html-greenhouse-embed (src#1373).",
    },
    {
        "employer": 'Seetec',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#916).",
    },
    {
        "employer": 'Severn Trent',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu/career?site=&company=severntrent&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu/career?site=&company=severntrent&lang=en%5FGB&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#591).",
    },
    {
        "employer": 'Smartest Energy',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://smartestenergy.teamtailor.com/connect/login',
        "hostname": 'smartestenergy.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://smartestenergy.teamtailor.com/connect/login',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#920).",
    },
    {
        "employer": 'Societe Generale International Limited',
        "action": "reclassify",
        "adapter_name": 'taleo',
        "ats_family": 'taleo',
        "base_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        "hostname": 'socgen.taleo.net',
        "config_blob": {
            "job_board_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        },
        "reason": "Source audit 2026-04-24: generic_site → taleo via html-taleo (src#603).",
    },
    {
        "employer": 'Société Générale (Germany)',
        "action": "reclassify",
        "adapter_name": 'taleo',
        "ats_family": 'taleo',
        "base_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        "hostname": 'socgen.taleo.net',
        "config_blob": {
            "job_board_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        },
        "reason": "Source audit 2026-04-24: generic_site → taleo via html-taleo (src#808).",
    },
    {
        "employer": 'Société Générale (UK)',
        "action": "reclassify",
        "adapter_name": 'taleo',
        "ats_family": 'taleo',
        "base_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        "hostname": 'socgen.taleo.net',
        "config_blob": {
            "job_board_url": 'https://socgen.taleo.net/careersection/sgcareers/profile.ftl?lang=&src=CWS-1',
        },
        "reason": "Source audit 2026-04-24: generic_site → taleo via html-taleo (src#809).",
    },
    {
        "employer": 'Sopra Steria',
        "action": "reclassify",
        "adapter_name": 'smartrecruiters',
        "ats_family": 'smartrecruiters',
        "base_url": 'https://careers.smartrecruiters.com/SopraSteria1/ssg_canada_en',
        "hostname": 'careers.smartrecruiters.com',
        "config_blob": {
            "slug": 'SopraSteria1',
            "job_board_url": 'https://careers.smartrecruiters.com/SopraSteria1',
        },
        "reason": "Source audit 2026-04-24: generic_site → smartrecruiters via html-smartrecruiters (src#922).",
    },
    {
        "employer": 'Stifel',
        "action": "reclassify",
        "adapter_name": 'icims',
        "ats_family": 'icims',
        "base_url": 'https://careers-stifel.icims.com/jobs/search?mode=job&iis=Company+Website&mobile=false&width=1170&height=500&bga=true&needsRedirect=false&jan1offset=-360&jun1offset=-300',
        "hostname": 'careers-stifel.icims.com',
        "config_blob": {
            "job_board_url": 'https://careers-stifel.icims.com/jobs/search?mode=job&iis=Company+Website&mobile=false&width=1170&height=500&bga=true&needsRedirect=false&jan1offset=-360&jun1offset=-300',
        },
        "reason": "Source audit 2026-04-24: generic_site → icims via html-icims (src#614).",
    },
    {
        "employer": 'Swiss Re',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://agency2.successfactors.eu/xi/ui/agency/pages/home.xhtml',
        "hostname": 'agency2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://agency2.successfactors.eu/xi/ui/agency/pages/home.xhtml',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#621).",
    },
    {
        "employer": 'The Ardonagh Group',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/Afe9ciQuWSc@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/Afe9ciQuWSc@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#629).",
    },
    {
        "employer": 'Transferroom',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/S7s9N-CISdQ@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/S7s9N-CISdQ@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#933).",
    },
    {
        "employer": 'Tripadvisor',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://boards.greenhouse.io/tripadvisor/jobs/4936629',
        "hostname": 'boards.greenhouse.io',
        "config_blob": {
            "slug": 'tripadvisor',
            "job_board_url": 'https://boards.greenhouse.io/tripadvisor',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#936).",
    },
    {
        "employer": 'Tullow Oil',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu/career?company=tullow&site=&lang=en_GB',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu/career?company=tullow&site=&lang=en_GB',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#938).",
    },
    {
        "employer": 'UK Power Networks',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#660).",
    },
    {
        "employer": 'Virgin Money',
        "action": "reclassify",
        "adapter_name": 'oracle',
        "ats_family": 'oracle',
        "base_url": 'https://dnn.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Nationwide/my-profile/sign-in',
        "hostname": 'dnn.fa.em2.oraclecloud.com',
        "config_blob": {
            "job_board_url": 'https://dnn.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Nationwide/my-profile/sign-in',
        },
        "reason": "Source audit 2026-04-24: generic_site → oracle via html-oracle (src#672).",
    },
    {
        "employer": 'Vistra',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career5.successfactors.eu',
        "hostname": 'career5.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career5.successfactors.eu',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#675).",
    },
    {
        "employer": 'Wickes',
        "action": "reclassify",
        "adapter_name": 'avature',
        "ats_family": 'avature',
        "base_url": 'https://wickes.avature.net/careers/Login',
        "hostname": 'wickes.avature.net',
        "config_blob": {
            "job_board_url": 'https://wickes.avature.net/careers/Login',
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Source audit 2026-04-24: generic_site → avature via html-avature (src#950).",
    },
    {
        "employer": 'William Hill',
        "action": "reclassify",
        "adapter_name": 'avature',
        "ats_family": 'avature',
        "base_url": 'https://amswh.avature.net/careers/JobDetail/senior-software-developer-12m-ftc/36335',
        "hostname": 'amswh.avature.net',
        "config_blob": {
            "job_board_url": 'https://amswh.avature.net/careers/JobDetail/senior-software-developer-12m-ftc/36335',
            "use_firefox": False,
            "max_pages": 5,
        },
        "reason": "Source audit 2026-04-24: generic_site → avature via html-avature (src#951).",
    },
    {
        "employer": 'XTX Markets',
        "action": "reclassify",
        "adapter_name": 'greenhouse',
        "ats_family": 'greenhouse',
        "base_url": 'https://job-boards.greenhouse.io/xtxmarketstechnologies',
        "hostname": 'job-boards.greenhouse.io',
        "config_blob": {
            "slug": 'xtxmarketstechnologies',
            "job_board_url": 'https://boards.greenhouse.io/xtxmarketstechnologies',
        },
        "reason": "Source audit 2026-04-24: generic_site → greenhouse via html-greenhouse-embed (src#689).",
    },
    {
        "employer": 'Zenobe',
        "action": "reclassify",
        "adapter_name": 'teamtailor',
        "ats_family": 'teamtailor',
        "base_url": 'https://app.teamtailor.com/companies/dFaSgQ2YwjY@eu/dashboard',
        "hostname": 'app.teamtailor.com',
        "config_blob": {
            "job_board_url": 'https://app.teamtailor.com/companies/dFaSgQ2YwjY@eu/dashboard',
        },
        "reason": "Source audit 2026-04-24: generic_site → teamtailor via html-teamtailor (src#694).",
    },
    {
        "employer": 'Zurich',
        "action": "reclassify",
        "adapter_name": 'successfactors',
        "ats_family": 'successfactors',
        "base_url": 'https://career2.successfactors.eu/career?site=&company=SF2013&lang=en%5FUS&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        "hostname": 'career2.successfactors.eu',
        "config_blob": {
            "job_board_url": 'https://career2.successfactors.eu/career?site=&company=SF2013&lang=en%5FUS&login_ns=register&career_ns=home&navBarLevel=MY_PROFILE',
        },
        "reason": "Source audit 2026-04-24: generic_site → successfactors via html-successfactors (src#695).",
    },
]


def _config_matches(current: dict | None, desired: dict) -> bool:
    """Return True if the source's config already contains every desired key=value."""
    if not isinstance(current, dict):
        return False
    return all(current.get(k) == v for k, v in desired.items())


def _apply_reclassify(session, src: Source, rule: dict, commit: bool) -> str:
    """Return a one-line outcome description for this source."""
    desired_config = rule["config_blob"]
    already_correct = (
        src.adapter_name == rule["adapter_name"]
        and src.ats_family == rule["ats_family"]
        and src.base_url == rule["base_url"]
        and src.hostname == rule["hostname"]
        and _config_matches(src.config_blob, desired_config)
    )
    if already_correct:
        return f"  skip   id={src.id:<5} already correct"

    if not commit:
        return (
            f"  would  id={src.id:<5} "
            f"{src.adapter_name} → {rule['adapter_name']}, "
            f"base_url: {src.base_url} → {rule['base_url']}"
        )

    src.adapter_name = rule["adapter_name"]
    src.ats_family = rule["ats_family"]
    src.base_url = rule["base_url"]
    src.hostname = rule["hostname"]
    # Merge keeps any operator-supplied extras the seeder added
    merged = dict(src.config_blob or {})
    merged.update(desired_config)
    src.config_blob = merged
    src.notes = (src.notes or "") + f" [2026-04-24 correction: {rule['reason']}]"
    src.updated_at = datetime.now(timezone.utc)
    return f"  fix    id={src.id:<5} reclassified to {rule['adapter_name']}"


def _apply_deactivate(src: Source, rule: dict, commit: bool) -> str:
    if not src.active:
        return f"  skip   id={src.id:<5} already inactive"
    if not commit:
        return f"  would  id={src.id:<5} deactivate ({src.adapter_name})"

    src.active = False
    src.archived_at = datetime.now(timezone.utc)
    src.notes = (src.notes or "") + f" [2026-04-24 correction: {rule['reason']}]"
    src.updated_at = datetime.now(timezone.utc)
    return f"  fix    id={src.id:<5} deactivated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply seeded-source corrections.")
    parser.add_argument("--commit", action="store_true", help="Write changes. Default: dry-run.")
    args = parser.parse_args()

    print(
        f"Apply source corrections — {len(_CORRECTIONS)} rule(s) "
        f"[{'COMMIT' if args.commit else 'DRY-RUN'}]"
    )

    total_fixed = 0
    total_skipped = 0
    total_missing = 0

    with SessionLocal() as session:
        for rule in _CORRECTIONS:
            employer = rule["employer"]
            action = rule["action"]
            print(f"\n[{action}] {employer} — {rule['reason']}")

            matches = list(session.execute(
                select(Source).where(func.lower(Source.employer_name) == employer.lower())
            ).scalars())

            if not matches:
                print(f"  miss   no Source rows found for employer={employer!r}")
                total_missing += 1
                continue

            for src in matches:
                if action == "reclassify":
                    msg = _apply_reclassify(session, src, rule, args.commit)
                elif action == "deactivate":
                    msg = _apply_deactivate(src, rule, args.commit)
                else:
                    msg = f"  err    unknown action={action!r}"
                print(msg)
                if msg.startswith("  fix") or msg.startswith("  would"):
                    total_fixed += 1
                elif msg.startswith("  skip"):
                    total_skipped += 1

        if args.commit:
            session.commit()
            print(f"\nDone. fixed={total_fixed}, already-correct={total_skipped}, missing-employer={total_missing}")
        else:
            print(f"\nDry-run complete. would-fix={total_fixed}, already-correct={total_skipped}, missing-employer={total_missing}")
            print("(pass --commit to write)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
