"""Unit tests for Phase 2.5 iframe URL extraction in
``scripts/audit_source_classifications.py``.

Each test covers one ATS bucket with a minimal hand-crafted HTML snippet
that simulates what the real careers page has when embedding that ATS.
The snippets are deliberately tiny — the goal is to pin the regex +
config-synthesis behaviour, not to assert HTML shape fidelity.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "audit_source_classifications.py"


def _load_audit_module():
    """Import the audit script as a module (it lives in scripts/, not src/)."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "audit_source_classifications_under_test", str(SCRIPT_PATH)
    )
    assert spec and spec.loader, "failed to load audit script"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load_audit_module()


# ── Fixtures — minimal HTML snippets per ATS bucket ──────────────────────

_HTML_FIXTURES: dict[str, str] = {
    # Real iframe URL shape from an Allstate-style page: tenant subdomain,
    # no locale segment, site_path + /login action suffix.
    "workday_no_locale": (
        '<a href="https://allstate.wd5.myworkdayjobs.com/allstate_careers/login">apply</a>'
    ),
    # The Hartford-style URL: locale-prefixed, classic shape.
    "workday_with_locale": (
        '<iframe src="https://thehartford.wd5.myworkdayjobs.com/en-US/Careers_External">'
    ),
    # Greenhouse native — the usual careers-page link shape.
    "greenhouse_native": (
        '<a href="https://job-boards.greenhouse.io/cais/jobs/123">view job</a>'
    ),
    # Greenhouse embed-script form (Point72 / Stripe style) — this is the
    # case the native pattern would wrongly capture slug="embed" if the
    # embed pattern isn't prioritised.
    "greenhouse_embed_script": (
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe"></script>'
    ),
    "lever": '<a href="https://jobs.lever.co/plaid/abc-123">apply</a>',
    # SF page with BOTH blacklisted asset hosts AND a real careers URL.
    # The extractor must skip rmkcdn and pick the careers URL.
    "successfactors_with_blacklist": (
        '<link href="//rmkcdn.successfactors.com/336/x.css">'
        '<script src="https://jobs.acme.successfactors.com/careers?company=acme"></script>'
    ),
    # iCIMS with the `jobs.{slug}.icims.com` shape (Shell uses this).
    "icims_jobs_prefix": '<iframe src="https://jobs.shell.icims.com/jobs/intro">',
    # Plain slug.icims.com shape.
    "icims_plain": '<iframe src="https://acme.icims.com/jobs/search">',
    "teamtailor_with_query": (
        '<iframe src="https://klarna.teamtailor.com/jobs?q=&amp;options=">'
    ),
    # Avature — both the careers-landing page href AND an iframe to the
    # actual Avature tenant. Extractor should pick the avature.net URL.
    "avature": (
        '<a href="https://jobs.metlife.com/details">job</a>'
        '<iframe src="https://metlife.avature.net/careers">'
    ),
    "oracle_cloud": (
        '<iframe src="https://jpmc.fa.eu8.oraclecloud.com/hcmUI/'
        'CandidateExperience/en/sites/CX_1001">'
    ),
    "pinpoint": '<iframe src="https://britishbusiness.pinpointhq.com/">',
    "eightfold": '<iframe src="https://hsbc.eightfold.ai/careers">',
    "smartrecruiters": '<iframe src="https://careers.smartrecruiters.com/soprasteria">',
    "workable": '<iframe src="https://apply.workable.com/acme-corp/">',
    "ashby": '<iframe src="https://jobs.ashbyhq.com/ramp">',
    "taleo": '<iframe src="https://voyacareers.taleo.net/careersection/2/jobsearch.ftl">',
}


# ── Tests: _extract_embed_url finds the right URL ────────────────────────

@pytest.mark.parametrize(
    "adapter,fixture,expect_substr",
    [
        ("workday", "workday_no_locale", "allstate.wd5.myworkdayjobs.com"),
        ("workday", "workday_with_locale", "thehartford.wd5.myworkdayjobs.com"),
        ("greenhouse", "greenhouse_native", "job-boards.greenhouse.io/cais"),
        ("greenhouse", "greenhouse_embed_script", "boards.greenhouse.io/stripe"),
        ("lever", "lever", "jobs.lever.co/plaid"),
        ("successfactors", "successfactors_with_blacklist", "acme.successfactors.com"),
        ("icims", "icims_jobs_prefix", "jobs.shell.icims.com"),
        ("icims", "icims_plain", "acme.icims.com"),
        ("teamtailor", "teamtailor_with_query", "klarna.teamtailor.com"),
        ("avature", "avature", "metlife.avature.net"),
        ("oracle", "oracle_cloud", "jpmc.fa.eu8.oraclecloud.com"),
        ("pinpoint", "pinpoint", "britishbusiness.pinpointhq.com"),
        ("eightfold", "eightfold", "hsbc.eightfold.ai"),
        ("smartrecruiters", "smartrecruiters", "careers.smartrecruiters.com/soprasteria"),
        ("workable", "workable", "apply.workable.com/acme-corp"),
        ("ashby", "ashby", "jobs.ashbyhq.com/ramp"),
        ("taleo", "taleo", "voyacareers.taleo.net"),
    ],
)
def test_extract_embed_url(adapter, fixture, expect_substr):
    url = audit._extract_embed_url(_HTML_FIXTURES[fixture], adapter)
    assert url is not None, f"{adapter}/{fixture}: expected a URL, got None"
    assert expect_substr in url, (
        f"{adapter}/{fixture}: expected '{expect_substr}' in URL, got '{url}'"
    )


# ── Tests: _build_config_blob produces adapter-correct shapes ────────────

def test_workday_config_includes_endpoint_and_site_path():
    url = audit._extract_embed_url(_HTML_FIXTURES["workday_no_locale"], "workday")
    cfg = audit._build_config_blob("workday", url)
    assert cfg is not None
    assert cfg["tenant"] == "allstate"
    assert cfg["shard"] == "wd5"
    # The action suffix /login must NOT have leaked into site_path
    assert cfg["site_path"] == "allstate_careers", (
        f"site_path picked up the action suffix: {cfg!r}"
    )
    assert cfg["endpoint_url"] == (
        "https://allstate.wd5.myworkdayjobs.com/wday/cxs/allstate/allstate_careers/jobs"
    )
    assert "job_board_url" in cfg


def test_workday_config_preserves_locale_and_site():
    url = audit._extract_embed_url(_HTML_FIXTURES["workday_with_locale"], "workday")
    cfg = audit._build_config_blob("workday", url)
    assert cfg is not None
    assert cfg["site_path"] == "Careers_External"
    assert cfg["tenant"] == "thehartford"
    assert "en-US" in cfg["job_board_url"]


@pytest.mark.parametrize(
    "adapter,fixture,expected_slug",
    [
        ("greenhouse", "greenhouse_native", "cais"),
        ("greenhouse", "greenhouse_embed_script", "stripe"),
        ("lever", "lever", "plaid"),
        ("smartrecruiters", "smartrecruiters", "soprasteria"),
        ("workable", "workable", "acme-corp"),
        ("ashby", "ashby", "ramp"),
    ],
)
def test_slug_adapters(adapter, fixture, expected_slug):
    url = audit._extract_embed_url(_HTML_FIXTURES[fixture], adapter)
    cfg = audit._build_config_blob(adapter, url)
    assert cfg is not None, f"{adapter}/{fixture}: cfg is None"
    assert cfg["slug"] == expected_slug
    assert cfg["job_board_url"].endswith(expected_slug) or expected_slug in cfg["job_board_url"]


@pytest.mark.parametrize(
    "adapter,fixture,expect_host_substr",
    [
        ("icims", "icims_jobs_prefix", "shell.icims.com"),
        ("icims", "icims_plain", "acme.icims.com"),
        ("teamtailor", "teamtailor_with_query", "klarna.teamtailor.com"),
        ("avature", "avature", "metlife.avature.net"),
        ("oracle", "oracle_cloud", "jpmc.fa.eu8.oraclecloud.com"),
        ("pinpoint", "pinpoint", "britishbusiness.pinpointhq.com"),
        ("eightfold", "eightfold", "hsbc.eightfold.ai"),
        ("taleo", "taleo", "voyacareers.taleo.net"),
        ("successfactors", "successfactors_with_blacklist", "acme.successfactors.com"),
    ],
)
def test_url_only_adapters(adapter, fixture, expect_host_substr):
    url = audit._extract_embed_url(_HTML_FIXTURES[fixture], adapter)
    cfg = audit._build_config_blob(adapter, url)
    assert cfg is not None, f"{adapter}/{fixture}: cfg is None"
    assert expect_host_substr in cfg["job_board_url"]


def test_teamtailor_strips_query_and_fragment():
    url = audit._extract_embed_url(_HTML_FIXTURES["teamtailor_with_query"], "teamtailor")
    cfg = audit._build_config_blob("teamtailor", url)
    assert cfg is not None
    assert "?" not in cfg["job_board_url"]
    assert "#" not in cfg["job_board_url"]


# ── Negative tests — blacklist + invalid-slug defences ───────────────────

def test_successfactors_blacklists_asset_hosts():
    """rmkcdn / performancemanager are litter — must not be picked up alone."""
    only_asset_html = (
        '<link href="//rmkcdn.successfactors.com/x.css">'
        '<script src="https://performancemanager.successfactors.eu/verp/x.js"></script>'
    )
    url = audit._extract_embed_url(only_asset_html, "successfactors")
    assert url is None, (
        f"blacklisted asset host leaked through as URL: {url}"
    )


def test_greenhouse_embed_word_not_mistaken_for_slug():
    """`boards.greenhouse.io/embed/...` must not produce slug='embed'."""
    html = '<iframe src="https://boards.greenhouse.io/embed/job_board/js?for=widgets">'
    url = audit._extract_embed_url(html, "greenhouse")
    cfg = audit._build_config_blob("greenhouse", url) if url else None
    assert cfg is not None
    assert cfg["slug"] != "embed"
    assert cfg["slug"] == "widgets"


def test_asset_path_suffix_rejected():
    """URLs ending in .js / .css shouldn't be accepted as tenant URLs."""
    html = '<link rel="icon" href="https://foo.icims.com/favicon.ico">'
    url = audit._extract_embed_url(html, "icims")
    assert url is None, f"asset path leaked through: {url}"


# ── Config validation ────────────────────────────────────────────────────

def test_validate_config_blob_flags_missing_required_keys():
    incomplete = {"job_board_url": "https://x.wd5.myworkdayjobs.com/"}
    result = audit._validate_config_blob("workday", incomplete)
    assert result is not None
    assert "endpoint_url" in result


def test_validate_config_blob_passes_when_complete():
    complete = {
        "endpoint_url": "https://x.wd5.myworkdayjobs.com/wday/cxs/x/y/jobs",
        "job_board_url": "https://x.wd5.myworkdayjobs.com/en-US/y",
        "tenant": "x", "shard": "wd5", "site_path": "y",
    }
    assert audit._validate_config_blob("workday", complete) is None


# ── Unknown adapter returns None rather than crashing ────────────────────

def test_unknown_adapter_returns_none():
    assert audit._extract_embed_url("<html></html>", "not_a_real_adapter") is None
    assert audit._build_config_blob("not_a_real_adapter", "https://example.com/") is None
