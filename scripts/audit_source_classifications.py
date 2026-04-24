#!/usr/bin/env python3
"""Full-DB source classification audit with incremental persistence + resume.

### Why this replaces `--all-active` on redetect_failing_sources.py

The 2026-04-24 run of ``redetect_failing_sources.py --all-active`` hung
at 1,180/1,347 (one source held a dead socket open with no per-probe
timeout) and lost 2h 21m of work because the old script only wrote its
findings at the final summary. This script fixes the three defects:

  1. **Per-source timeout** — each probe is wrapped in asyncio.wait_for,
     so a hung request can't stall the whole run.
  2. **Incremental JSONL persistence** — every probed source is appended
     to an output file as soon as its verdict is known. A crash loses
     only the in-flight sources, not the whole run.
  3. **Resume capability** — on startup the script reads the JSONL and
     skips any source_id already processed, so killing + restarting
     just picks up where it left off.

Plus: URL pattern detection is extended to recognise Avature and njoyn
(the upstream detector in ``api/source_detector.py`` doesn't know about
either) so the audit classifies the 8 Avature tenants correctly in one
pass.

### Phases

For each source:

  * **Phase 1 — URL pattern** (instant, zero network). Runs the upstream
    ``detect_platform()`` + the extended patterns below. If this returns
    anything other than ``generic_site``, that's the verdict — no probe.
  * **Phase 2 — HTTP probe** (only for generic_site verdicts). Fetches
    the page with httpx (short timeout, browser headers) and greps for
    embed hints (Workday iframe, Greenhouse embed script, etc.). If one
    is found, upgrade the verdict.
  * **Phase 3 — reachability mark** (independent of classification).
    Records HTTP status + latency so we can separately spot dead boards.

All three phases run under a 30s wall-clock timeout per source.

### Output

JSONL at ``./artifacts/source_audit.jsonl`` by default. One line per
source with fields:

    {
      "source_id": 489,
      "source_key": "generic_site_metro_bank_xxxxx",
      "employer": "Metro Bank",
      "current_adapter": "generic_site",
      "base_url": "https://metrobank.avature.net/amazingcareers",
      "detected_adapter": "avature",
      "detection_signal": "hostname-pattern",  # or "html-embed", "upstream", "error"
      "transition": true,                      # current_adapter != detected_adapter
      "reachable": true,                       # Phase 3 — can the server be reached?
      "probe_status": 200,                     # HTTP code, None if not probed
      "probe_method": "phase1_url_pattern",    # or "phase2_html_embed", "pattern_plus_probe"
      "error": null,                           # or "timeout" / "dns_fail" / "ssl" etc.
      "probed_at": "2026-04-24T16:35:02Z"
    }

### Subcommands

    # Probe + write JSONL (resumable)
    python3 scripts/audit_source_classifications.py audit

    # Summarise a completed JSONL
    python3 scripts/audit_source_classifications.py summary

    # Emit correction-script entries from the JSONL
    python3 scripts/audit_source_classifications.py gen-corrections

### Safety

  * Zero DB writes. This script is purely a diagnostic / read-only probe.
  * Apply findings via the existing ``scripts/apply_source_corrections.py``
    (manual review of each before commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from vacancysoft.adapters.workday import derive_workday_candidate_endpoints  # noqa: E402
from vacancysoft.api.source_detector import detect_platform  # noqa: E402
from vacancysoft.db.engine import SessionLocal  # noqa: E402
from vacancysoft.db.models import Source  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "source_audit.jsonl"

# Map upstream detector names to our internal adapter_name values.
# Detector returns "oracle_cloud" but our adapter is registered as "oracle"
# (see src/vacancysoft/adapters/oracle_cloud.py — class adapter_name="oracle").
# Mirrors the ADAPTER_MAP in scripts/redetect_failing_sources.py.
_DETECTOR_TO_ADAPTER = {
    "oracle_cloud": "oracle",
    "generic_site": "generic_site",  # pass-through
    "adp_workforcenow": "adp",
    "greenhouse_embed": "greenhouse",
}

# Aggregator adapters — these must NEVER be downgraded to generic_site by
# the audit, even if the probe finds no ATS fingerprint. An aggregator
# "source" IS the aggregator API endpoint; it's not a company career page
# that embeds an ATS. Mirrors src/vacancysoft/api/ledger.py:36.
_AGGREGATOR_ADAPTERS = frozenset({
    "adzuna", "reed", "efinancialcareers", "google_jobs", "coresignal",
})

# ── Extended URL patterns the upstream detector doesn't know ───────────────
# Each entry: (compiled regex, adapter, signal tag)
_EXTENDED_HOSTNAME_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.avature\.net", re.I), "avature", "hostname-avature"),
    (re.compile(r"recruitment\.macquarie\.com", re.I), "avature", "hostname-macquarie-avature"),
    (re.compile(r"\.njoyn\.com", re.I), "njoyn", "hostname-njoyn"),
]

# HTML-embed fingerprints (Phase 2). Keyed by search regex, value is the
# inferred adapter name.
_EMBED_FINGERPRINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\.wd\d+\.myworkdayjobs\.com", re.I), "workday", "html-workday-iframe"),
    (re.compile(r"boards\.greenhouse\.io", re.I), "greenhouse", "html-greenhouse-embed"),
    (re.compile(r"jobs\.lever\.co", re.I), "lever", "html-lever-link"),
    (re.compile(r"\.icims\.com", re.I), "icims", "html-icims"),
    (re.compile(r"careers\.smartrecruiters\.com", re.I), "smartrecruiters", "html-smartrecruiters"),
    (re.compile(r"jobs\.ashbyhq\.com", re.I), "ashby", "html-ashby"),
    (re.compile(r"apply\.workable\.com", re.I), "workable", "html-workable"),
    (re.compile(r"\.avature\.net", re.I), "avature", "html-avature"),
    (re.compile(r"\.njoyn\.com", re.I), "njoyn", "html-njoyn"),
    (re.compile(r"\.oraclecloud\.com", re.I), "oracle_cloud", "html-oracle"),
    (re.compile(r"\.successfactors\.", re.I), "successfactors", "html-successfactors"),
    (re.compile(r"\.eightfold\.ai", re.I), "eightfold", "html-eightfold"),
    (re.compile(r"\.teamtailor\.com", re.I), "teamtailor", "html-teamtailor"),
    (re.compile(r"\.taleo\.net", re.I), "taleo", "html-taleo"),
    (re.compile(r"\.pinpointhq\.com", re.I), "pinpoint", "html-pinpoint"),
    (re.compile(r"\.hibob\.com", re.I), "hibob", "html-hibob"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Upgrade-Insecure-Requests": "1",
}


# ── Phase 2.5 — iframe URL extraction + per-adapter config synthesis ────────
#
# When Phase 2 detects an embed hint (e.g. "this page contains a Workday
# iframe"), that's only half the answer. The iframe URL itself is what the
# target adapter needs — e.g. Workday requires endpoint_url / tenant / shard /
# site_path, Greenhouse requires slug. This section extracts the URL from the
# rendered HTML and synthesises the full source_config blob.

# Asset / CDN hosts to REJECT when scanning for a tenant URL. Hit most often
# on SuccessFactors sites where rmkcdn / performancemanager hosts litter the
# HTML but aren't the tenant's job-board URL.
_ASSET_HOST_BLACKLIST: dict[str, frozenset[str]] = {
    "successfactors": frozenset({
        "rmkcdn.successfactors.com",
        "rmkcdn.successfactors.eu",
        "performancemanager.successfactors.eu",
        "performancemanager.successfactors.com",
        "performancemanager4.successfactors.eu",
        "performancemanager4.successfactors.com",
        "performancemanager8.successfactors.eu",
        "performancemanager8.successfactors.com",
    }),
    "icims": frozenset({"s.icims.com"}),
    "adp": frozenset({"static.workforcenow.adp.com"}),
    "workday": frozenset({"static.myworkdaycdn.com"}),
}

# URL patterns used to find the tenant URL inside careers-page HTML. Each
# pattern captures either a named `slug` or `tenant` group used by the
# config synthesiser. `detected_adapter` key uses our internal adapter_name.
_URL_EXTRACTORS: dict[str, re.Pattern[str]] = {
    # Workday tenant URLs. Constrained locale group (e.g. en-US, fr-FR,
    # en_US with underscore) so "allstate_careers" doesn't accidentally
    # get swallowed as a locale, leaving "login" as the site. Trailing
    # path tail matched but not captured (action suffixes like /login).
    "workday": re.compile(
        r"https?://(?P<tenant>[\w-]+)\.(?P<shard>wd\d+)\.myworkdayjobs\.com/"
        r"(?:(?P<locale>[a-z]{2}[-_][A-Za-z]{2,4})/)?"
        r"(?P<site>[\w-]+)"
        r"(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "greenhouse": re.compile(
        r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/"
        r"(?P<slug>[\w-]+)(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "greenhouse_embed": re.compile(
        r"greenhouse\.io/embed/job_board/[^\s\"'<>\\]*for=(?P<slug>[\w-]+)",
        re.IGNORECASE,
    ),
    "lever": re.compile(
        r"https?://jobs\.lever\.co/(?P<slug>[\w-]+)(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    # Matches `slug.icims.com` or `jobs.slug.icims.com` shapes. The last
    # labelled subdomain before `.icims.com` is captured as slug.
    "icims": re.compile(
        r"https?://(?:[\w-]+\.)*(?P<slug>[\w-]+)\.icims\.com(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "smartrecruiters": re.compile(
        r"https?://careers\.smartrecruiters\.com/(?P<slug>[\w-]+)"
        r"(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "workable": re.compile(
        r"https?://apply\.workable\.com/(?P<slug>[\w-]+)(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "ashby": re.compile(
        r"https?://jobs\.ashbyhq\.com/(?P<slug>[\w-]+)(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "teamtailor": re.compile(
        r"https?://(?P<slug>[\w-]+)\.teamtailor\.com(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "taleo": re.compile(
        r"https?://[\w.-]+\.taleo\.net(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "successfactors": re.compile(
        r"https?://[\w.-]+\.successfactors\.(?:com|eu)(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "oracle": re.compile(
        r"https?://[\w-]+\.fa\.[\w.-]+\.oraclecloud\.com(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "pinpoint": re.compile(
        r"https?://(?P<slug>[\w-]+)\.pinpointhq\.com(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "eightfold": re.compile(
        r"https?://(?P<slug>[\w-]+)\.eightfold\.ai(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "avature": re.compile(
        r"https?://(?P<tenant>[\w-]+)\.avature\.net(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
    "adp": re.compile(
        r"https?://(?:workforcenow\.adp\.com|[\w-]+\.careers\.adp\.com)"
        r"(?:/[^\s\"'<>\\]*)?",
        re.IGNORECASE,
    ),
}

# Required source_config keys per adapter. Config synthesis must produce each.
_REQUIRED_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "workday": ("endpoint_url", "job_board_url"),
    "greenhouse": ("slug",),
    "lever": ("slug",),
    "icims": ("job_board_url",),
    "smartrecruiters": ("slug",),
    "workable": ("slug",),
    "ashby": ("slug",),
    "teamtailor": ("job_board_url",),
    "taleo": ("job_board_url",),
    "successfactors": ("job_board_url",),
    "oracle": ("job_board_url",),
    "pinpoint": ("job_board_url",),
    "eightfold": ("job_board_url",),
    "avature": ("job_board_url",),
    "adp": ("job_board_url",),
    "njoyn": ("job_board_url",),
}

# File extensions on the URL path that mean "this is an asset, not a job board".
_ASSET_PATH_SUFFIXES = (
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".ico", ".svg", ".gif",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".json",
)

# Slugs that are never real ATS slugs — typically artefacts of matching the
# wrong URL shape (e.g. `boards.greenhouse.io/embed/job_board/js?for=X`
# where the native pattern wrongly captures slug="embed").
_INVALID_SLUGS: frozenset[str] = frozenset({
    "embed", "js", "api", "widget", "static", "assets", "login", "logout",
    "apply", "sign_in", "register",
    # "www" catches the Teamtailor "powered by" marketing URL shape where
    # the promo badge on a careers page points at `www.teamtailor.com/...`
    # rather than the tenant's actual subdomain.
    "www", "portal", "cdn",
})


def _extract_embed_url(html: str, adapter: str) -> str | None:
    """Return the first tenant URL in `html` matching `adapter`, skipping assets.

    Greenhouse is checked via BOTH the native pattern and the embed_job_board
    script pattern so sites like Point72 (which use the embed form) resolve.
    """
    patterns: list[re.Pattern[str]] = []
    if adapter == "greenhouse":
        # Embed pattern FIRST — it's more specific than the native one.
        # Without this priority, a URL like `/embed/job_board/js?for=stripe`
        # matches the native pattern with slug="embed" (wrong).
        patterns.append(_URL_EXTRACTORS["greenhouse_embed"])
        patterns.append(_URL_EXTRACTORS["greenhouse"])
    elif adapter in _URL_EXTRACTORS:
        patterns.append(_URL_EXTRACTORS[adapter])
    else:
        return None

    blacklist = _ASSET_HOST_BLACKLIST.get(adapter, frozenset())
    sample = html[:200_000] if len(html) > 200_000 else html
    for pattern in patterns:
        for match in pattern.finditer(sample):
            url = match.group(0).strip()
            if not url.startswith(("http://", "https://")):
                # The greenhouse_embed pattern captures just the
                # `greenhouse.io/embed/...for=slug` fragment without a scheme.
                # Re-derive a canonical board URL using the captured slug.
                if (
                    adapter == "greenhouse"
                    and pattern is _URL_EXTRACTORS["greenhouse_embed"]
                    and "slug" in match.groupdict()
                ):
                    slug = match.group("slug")
                    if slug and slug.lower() not in _INVALID_SLUGS:
                        return f"https://boards.greenhouse.io/{slug}"
                continue
            # Skip if captured slug is obviously wrong (e.g. "embed")
            gd = match.groupdict()
            slug = gd.get("slug") or ""
            if slug and slug.lower() in _INVALID_SLUGS:
                continue
            host = urlparse(url).netloc.lower()
            if host in blacklist:
                continue
            path = urlparse(url).path.lower()
            if any(path.endswith(suf) for suf in _ASSET_PATH_SUFFIXES):
                continue
            return url
    return None


def _build_config_blob(adapter: str, embed_url: str) -> dict[str, Any] | None:
    """Synthesise the source_config blob for the given adapter from an embed URL.

    Returns None when the URL doesn't fit the expected shape. Each branch
    mirrors the adapter's documented config contract (see plan file for
    the full matrix).
    """
    if adapter == "workday":
        # Parse tenant/shard/site via our constrained regex so action suffixes
        # (/login, /apply) and missing locales (allstate_careers/login shape)
        # don't confuse the site_path extraction.
        m = _URL_EXTRACTORS["workday"].search(embed_url)
        if not m:
            return None
        tenant = m.group("tenant")
        shard = m.group("shard")
        site = m.group("site")
        if not (tenant and shard and site):
            return None
        # Canonical job_board_url — use locale if present, else default en-US
        # (Workday's landing redirects locales under the hood; en-US is safe).
        locale = m.groupdict().get("locale") or "en-US"
        canonical_board = (
            f"https://{tenant}.{shard}.myworkdayjobs.com/{locale}/{site}"
        )
        endpoint_url = (
            f"https://{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        )
        # Sanity-check by asking the adapter's own derive fn if it would
        # produce any endpoints for our canonical URL. If it wouldn't,
        # our build is probably malformed.
        if not derive_workday_candidate_endpoints(canonical_board):
            return None
        return {
            "endpoint_url": endpoint_url,
            "job_board_url": canonical_board,
            "tenant": tenant,
            "shard": shard,
            "site_path": site,
        }

    if adapter in ("greenhouse", "lever", "smartrecruiters", "workable", "ashby"):
        pattern = _URL_EXTRACTORS[adapter]
        m = pattern.search(embed_url)
        if not m or "slug" not in m.groupdict():
            return None
        slug = m.group("slug")
        canonical = {
            "greenhouse": f"https://boards.greenhouse.io/{slug}",
            "lever": f"https://jobs.lever.co/{slug}",
            "smartrecruiters": f"https://careers.smartrecruiters.com/{slug}",
            "workable": f"https://apply.workable.com/{slug}",
            "ashby": f"https://jobs.ashbyhq.com/{slug}",
        }[adapter]
        return {"slug": slug, "job_board_url": canonical}

    if adapter == "teamtailor":
        parsed = urlparse(embed_url)
        # Strip query + fragment — matches _derive_rss_url in teamtailor.py
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return {"job_board_url": clean}

    if adapter == "avature":
        return {
            "job_board_url": embed_url,
            "use_firefox": False,
            "max_pages": 5,
        }

    if adapter in ("icims", "taleo", "successfactors", "oracle", "pinpoint",
                   "eightfold", "adp", "njoyn"):
        return {"job_board_url": embed_url}

    return None


def _validate_config_blob(adapter: str, config: dict[str, Any]) -> str | None:
    """Return None if valid; otherwise a one-line reason string."""
    required = _REQUIRED_CONFIG_KEYS.get(adapter, ())
    missing = [k for k in required if not config.get(k)]
    if missing:
        return f"missing required keys: {', '.join(missing)}"
    return None


def _extended_hostname_detect(url: str) -> tuple[str, str] | None:
    """Return (adapter, signal) if URL hostname matches an extended pattern."""
    host_path = urlparse(url).netloc + urlparse(url).path
    for pattern, adapter, signal in _EXTENDED_HOSTNAME_PATTERNS:
        if pattern.search(host_path):
            return adapter, signal
    return None


def _phase1_url_pattern(url: str) -> dict[str, Any]:
    """URL-only detection. Prefers the upstream detector; falls back to extended patterns."""
    upstream = detect_platform(url)
    adapter = upstream["adapter"] if upstream else "generic_site"
    signal = "upstream-pattern"
    if adapter == "generic_site":
        ext = _extended_hostname_detect(url)
        if ext:
            adapter, signal = ext
    # Normalise to our internal adapter_name (detector uses "oracle_cloud"
    # but we register "oracle"; similar shim for adp variants).
    adapter = _DETECTOR_TO_ADAPTER.get(adapter, adapter)
    return {"adapter": adapter, "signal": signal}


def _phase2_html_embed(html: str) -> tuple[str, str] | None:
    """Return (adapter, signal) if HTML contains a recognisable ATS fingerprint."""
    # Only look at the first 200KB — enough to catch iframe srcs + embed scripts
    sample = html[:200_000] if len(html) > 200_000 else html
    for pattern, adapter, signal in _EMBED_FINGERPRINTS:
        if pattern.search(sample):
            return adapter, signal
    return None


# ── Firefox fallback for JS-rendered pages ──────────────────────────────────
# Mirrors the pattern in scripts/backfill_avature_locations.py::_FirefoxFetcher
# but async so it fits the audit's asyncio.gather loop. One instance per run —
# browser + context reused across all sources so startup cost is paid once.
class _FirefoxFetcher:
    def __init__(self, *, page_timeout_ms: int = 25000, settle_ms: int = 2500):
        self._p = None
        self._browser = None
        self._context = None
        self._started = False
        self.page_timeout_ms = page_timeout_ms
        self.settle_ms = settle_ms

    async def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True  # guard against concurrent starts
        from playwright.async_api import async_playwright
        self._p = await async_playwright().start()
        self._browser = await self._p.firefox.launch(headless=True)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:131.0) "
                "Gecko/20100101 Firefox/131.0"
            ),
            viewport={"width": 1400, "height": 900},
            locale="en-US",
        )

    async def fetch(self, url: str) -> str | None:
        """Return the rendered HTML, or None on any failure. Never raises."""
        try:
            await self._ensure_started()
        except Exception:
            return None
        page = None
        try:
            page = await self._context.new_page()
            try:
                await page.goto(url, wait_until="networkidle",
                                timeout=self.page_timeout_ms)
            except Exception:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    return None
            await asyncio.sleep(self.settle_ms / 1000.0)
            return await page.content()
        except Exception:
            return None
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._p is not None:
                await self._p.stop()
        except Exception:
            pass


async def _probe_url(url: str, timeout: float) -> dict[str, Any]:
    """Single httpx GET with browser headers. Never raises."""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "status": resp.status_code,
                "latency_ms": latency_ms,
                "body": resp.text if resp.status_code < 400 else "",
                "error": None,
                "final_url": str(resp.url),
            }
    except httpx.TimeoutException:
        return {"status": None, "latency_ms": int((time.perf_counter() - t0) * 1000), "body": "", "error": "timeout", "final_url": url}
    except httpx.ConnectError as exc:
        return {"status": None, "latency_ms": None, "body": "", "error": f"connect: {str(exc)[:100]}", "final_url": url}
    except Exception as exc:  # catch-all, script must not die mid-source
        return {"status": None, "latency_ms": None, "body": "", "error": f"{type(exc).__name__}: {str(exc)[:100]}", "final_url": url}


async def _audit_one(
    src_row: dict[str, Any],
    *,
    probe_timeout: float,
    firefox: _FirefoxFetcher | None = None,
) -> dict[str, Any]:
    """Return the full audit dict for one Source row."""
    url = (src_row["base_url"] or "").strip()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base = {
        "source_id": src_row["id"],
        "source_key": src_row["source_key"],
        "employer": src_row["employer_name"],
        "current_adapter": src_row["adapter_name"],
        "base_url": url,
        "probed_at": now,
    }
    if not url:
        return base | {
            "detected_adapter": None, "detection_signal": "no-url",
            "transition": False, "reachable": False,
            "probe_status": None, "probe_method": "phase1_url_pattern", "error": "no url",
            "detected_embed_url": None, "detected_embed_method": None,
            "proposed_config_blob": None, "confidence": "low",
            "manual_review_reason": "source has no base_url",
        }

    # Phase 1 — URL pattern
    phase1 = _phase1_url_pattern(url)

    # Phase 2 — probe for HTML embed hints ONLY if phase1 landed on generic_site
    detected = phase1["adapter"]
    signal = phase1["signal"]
    probe_status: int | None = None
    probe_error: str | None = None
    probe_method = "phase1_url_pattern"
    reachable = True  # optimistic; updated if we actually probe
    probe_body: str = ""

    if phase1["adapter"] == "generic_site":
        probe = await _probe_url(url, probe_timeout)
        probe_status = probe["status"]
        probe_error = probe["error"]
        reachable = probe["status"] is not None and probe["status"] < 500
        probe_body = probe.get("body") or ""
        if probe_body:
            embed = _phase2_html_embed(probe_body)
            if embed:
                detected_raw, signal = embed
                detected = _DETECTOR_TO_ADAPTER.get(detected_raw, detected_raw)
                probe_method = "phase2_html_embed"
            else:
                probe_method = "pattern_plus_probe"
        else:
            probe_method = "pattern_plus_probe"

    # Aggregator protection — refuse to downgrade an aggregator source to
    # generic_site just because its server URL doesn't look like a career
    # page. Aggregator source rows point at API endpoints, not company sites.
    if (
        src_row["adapter_name"] in _AGGREGATOR_ADAPTERS
        and detected == "generic_site"
    ):
        detected = src_row["adapter_name"]
        signal = "aggregator-protected"
        probe_method = "aggregator_protected"

    # ATS-already-classified protection — if a source is currently on a known
    # ATS adapter and the detector only says "generic_site", DO NOT downgrade.
    # Common case: the Source.base_url is the company's careers landing page
    # (e.g. careers.wellsfargo.com) but the config_blob has the real ATS
    # endpoint (e.g. *.wd5.myworkdayjobs.com/wday/cxs/...). The adapter works
    # fine; we just can't confirm the ATS from base_url alone in Phase 1,
    # and Phase 2 didn't find an embed hint either. Keeping the current
    # classification is safer than blindly downgrading.
    if (
        src_row["adapter_name"] not in {"generic_site", None}
        and detected == "generic_site"
        and src_row["adapter_name"] not in _AGGREGATOR_ADAPTERS  # handled above
    ):
        detected = src_row["adapter_name"]
        signal = "current-adapter-protected"
        probe_method = "adapter_protected"

    transition = bool(detected) and detected != src_row["adapter_name"]

    # ── Phase 2.5 — extract embed URL + synthesise config_blob ──
    # Only runs when we have a real transition AND the detected adapter has
    # an extractor/builder. Three fallback tiers per transition:
    #   1. base_url IS the tenant URL (upstream-pattern / hostname-* signals).
    #      Build config directly from base_url.
    #   2. html-* signal — extract tenant URL from the httpx probe body.
    #   3. Firefox fallback — if tier 2 failed and the page might be JS-rendered.
    detected_embed_url: str | None = None
    detected_embed_method: str | None = None
    proposed_config_blob: dict[str, Any] | None = None
    confidence = "low"
    manual_review_reason: str | None = None

    if transition and detected and detected in _URL_EXTRACTORS:
        # Tier 1 — the base_url itself might already match the adapter pattern
        if _URL_EXTRACTORS[detected].search(url):
            cfg = _build_config_blob(detected, url)
            if cfg and _validate_config_blob(detected, cfg) is None:
                detected_embed_url = url
                detected_embed_method = "phase1_url_pattern"
                proposed_config_blob = cfg
                confidence = "high"

        # Tier 2 — extract from the httpx probe body
        if proposed_config_blob is None and probe_body:
            embed_url = _extract_embed_url(probe_body, detected)
            if embed_url:
                cfg = _build_config_blob(detected, embed_url)
                if cfg and _validate_config_blob(detected, cfg) is None:
                    detected_embed_url = embed_url
                    detected_embed_method = "phase2_html_httpx"
                    proposed_config_blob = cfg
                    confidence = "high"

        # Tier 3 — Firefox fallback (JS-rendered iframes)
        if proposed_config_blob is None and firefox is not None:
            ff_html = await firefox.fetch(url)
            if ff_html:
                embed_url = _extract_embed_url(ff_html, detected)
                if embed_url:
                    cfg = _build_config_blob(detected, embed_url)
                    if cfg and _validate_config_blob(detected, cfg) is None:
                        detected_embed_url = embed_url
                        detected_embed_method = "phase2_firefox"
                        proposed_config_blob = cfg
                        confidence = "high"

        if proposed_config_blob is None:
            manual_review_reason = (
                "no tenant URL could be extracted from base_url, "
                "httpx body, or Firefox render"
            )
    elif transition and detected:
        manual_review_reason = f"no config builder for adapter '{detected}'"
    elif not transition:
        # adapter already matches — no correction needed, confidence irrelevant
        confidence = "high"

    return base | {
        "detected_adapter": detected,
        "detection_signal": signal,
        "transition": transition,
        "reachable": reachable,
        "probe_status": probe_status,
        "probe_method": probe_method,
        "error": probe_error,
        "detected_embed_url": detected_embed_url,
        "detected_embed_method": detected_embed_method,
        "proposed_config_blob": proposed_config_blob,
        "confidence": confidence,
        "manual_review_reason": manual_review_reason,
    }


def _load_existing_source_ids(output_path: Path) -> set[int]:
    """Read the JSONL and return source_ids already processed (for resume)."""
    if not output_path.exists():
        return set()
    ids: set[int] = set()
    with output_path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
                ids.add(int(row["source_id"]))
            except Exception:
                continue
    return ids


def _load_candidate_rows(only_active: bool, limit: int | None) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        stmt = select(
            Source.id, Source.source_key, Source.employer_name,
            Source.adapter_name, Source.base_url, Source.active,
        )
        if only_active:
            stmt = stmt.where(Source.active.is_(True))
        stmt = stmt.order_by(Source.id)
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()
    return [
        {
            "id": r.id, "source_key": r.source_key, "employer_name": r.employer_name,
            "adapter_name": r.adapter_name, "base_url": r.base_url, "active": r.active,
        }
        for r in rows
    ]


async def _audit_with_timeout(
    src_row,
    probe_timeout: float,
    wall_timeout: float,
    firefox: _FirefoxFetcher | None = None,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _audit_one(src_row, probe_timeout=probe_timeout, firefox=firefox),
            timeout=wall_timeout,
        )
    except asyncio.TimeoutError:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "source_id": src_row["id"], "source_key": src_row["source_key"],
            "employer": src_row["employer_name"],
            "current_adapter": src_row["adapter_name"],
            "base_url": src_row["base_url"],
            "detected_adapter": None, "detection_signal": "wall-timeout",
            "transition": False, "reachable": False,
            "probe_status": None, "probe_method": "timeout", "error": "wall-timeout",
            "probed_at": now,
            "detected_embed_url": None, "detected_embed_method": None,
            "proposed_config_blob": None, "confidence": "low",
            "manual_review_reason": "audit per-source wall-timeout",
        }


def _row_needs_phase25_refresh(row: dict[str, Any]) -> bool:
    """Return True if a resumed JSONL row lacks Phase 2.5 fields AND should be re-probed.

    Old rows from PR #73 don't have `proposed_config_blob`/`confidence` etc.
    If the row represents a transition that could benefit, re-probe it.
    Non-transition rows stay as-is to preserve resume bandwidth.
    """
    if "proposed_config_blob" in row:
        return False  # already Phase 2.5 format
    return bool(row.get("transition")) and bool(row.get("detected_adapter"))


async def run_audit(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    only_active = not args.include_inactive
    candidates = _load_candidate_rows(only_active=only_active, limit=args.limit)

    already: set[int] = set()
    needs_refresh: set[int] = set()
    if args.resume and output_path.exists():
        for line in output_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            sid = row.get("source_id")
            if sid is None:
                continue
            if args.refresh_embed and _row_needs_phase25_refresh(row):
                needs_refresh.add(int(sid))
            else:
                already.add(int(sid))
    to_do = [r for r in candidates if r["id"] not in already]
    refresh_count = sum(1 for r in candidates if r["id"] in needs_refresh)

    ff_mode = "on (fallback)" if args.firefox_fallback else "off"
    print(
        f"Source audit — {len(candidates)} candidates "
        f"({'active only' if only_active else 'all'}), "
        f"{len(already)} already in {output_path.name}, "
        f"{len(to_do)} to process  |  firefox={ff_mode}"
    )
    if refresh_count:
        print(f"  (incl. {refresh_count} old-format rows queued for Phase 2.5 refresh)")
    if not to_do:
        print("  Nothing to do. Use `summary` to inspect findings.")
        return 0

    firefox = _FirefoxFetcher() if args.firefox_fallback else None
    sem = asyncio.Semaphore(args.concurrency)
    probed_counter = [0]  # mutable ref for the worker closure
    t_start = time.time()
    lock = asyncio.Lock()

    async def _worker(src_row: dict[str, Any]) -> None:
        async with sem:
            result = await _audit_with_timeout(
                src_row,
                probe_timeout=args.probe_timeout,
                wall_timeout=args.wall_timeout,
                firefox=firefox,
            )
        async with lock:
            with output_path.open("a") as f:
                f.write(json.dumps(result) + "\n")
            probed_counter[0] += 1
            if probed_counter[0] % 25 == 0 or probed_counter[0] == len(to_do):
                rate = probed_counter[0] / max(time.time() - t_start, 0.001) * 60
                remaining = len(to_do) - probed_counter[0]
                eta_min = remaining / max(rate, 0.01)
                print(
                    f"  …probed {probed_counter[0]}/{len(to_do)} "
                    f"({rate:.1f}/min, ETA {eta_min:.1f} min)"
                )

    try:
        await asyncio.gather(*(_worker(r) for r in to_do))
    finally:
        if firefox is not None:
            await firefox.close()

    print(f"\nDone. Wrote {len(to_do)} new rows to {output_path}")
    print("Next: `python3 scripts/audit_source_classifications.py summary`")
    return 0


# ── Summary subcommand ──────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def run_summary(args: argparse.Namespace) -> int:
    path = Path(args.output)
    rows = _read_jsonl(path)
    if not rows:
        print(f"No rows in {path}")
        return 1
    print(f"Audit summary — {len(rows)} source rows from {path}")

    transitions = [r for r in rows if r.get("transition")]
    unreachable = [r for r in rows if not r.get("reachable")]
    unchanged = [r for r in rows if not r.get("transition") and r.get("reachable")]

    auto_applicable = [
        r for r in transitions
        if r.get("confidence") == "high" and r.get("proposed_config_blob")
    ]
    manual_review = [
        r for r in transitions if r not in auto_applicable
    ]

    print(f"\n  transitions (reclassify candidates): {len(transitions)}")
    print(f"    ├─ auto-applicable (high-conf):    {len(auto_applicable)}")
    print(f"    └─ manual review (low-conf):       {len(manual_review)}")
    print(f"  unreachable / errored:                {len(unreachable)}")
    print(f"  unchanged (adapter already correct):  {len(unchanged)}")

    by_pair: Counter = Counter()
    for r in transitions:
        by_pair[(r.get("current_adapter"), r.get("detected_adapter"))] += 1

    if by_pair:
        print("\n=== Top transition pairs (current → detected) ===")
        for (cur, det), n in by_pair.most_common(15):
            print(f"  {cur:<20} → {det:<20}  {n}")

    by_signal: Counter = Counter(r.get("detection_signal") for r in transitions)
    if by_signal:
        print("\n=== Transitions by detection signal ===")
        for sig, n in by_signal.most_common():
            print(f"  {sig:<26}  {n}")

    if unreachable:
        print(f"\n=== First {min(10, len(unreachable))} unreachable sources ===")
        for r in unreachable[:10]:
            print(f"  src#{r['source_id']:<5}  {r['employer']:<32}  {r['base_url']:<60}  err={r.get('error')}")

    if transitions:
        print(f"\n=== First {min(10, len(transitions))} transitions ===")
        for r in transitions[:10]:
            print(
                f"  src#{r['source_id']:<5}  {r['employer']:<32}  "
                f"{r['current_adapter']:<14} → {r['detected_adapter']:<14}  ({r['detection_signal']})"
            )
    return 0


# ── gen-corrections subcommand ──────────────────────────────────────────────

def _format_correction_dict(
    employer: str, row: dict[str, Any], config_blob: dict[str, Any]
) -> str:
    """Emit one Python dict literal suitable for pasting into _CORRECTIONS."""
    detected = row["detected_adapter"]
    signal = row.get("detection_signal") or "unknown"
    source_id = row.get("source_id")
    embed_url = row.get("detected_embed_url") or row["base_url"]
    hostname = urlparse(embed_url).netloc or urlparse(row["base_url"]).netloc

    # Render each config key on its own line with consistent indentation.
    # Double quotes around keys to match the style elsewhere in _CORRECTIONS.
    cb_body = "\n".join(
        f'            "{k}": {v!r},' for k, v in config_blob.items()
    )
    cb_block = "{\n" + cb_body + "\n        }"

    return (
        "    {\n"
        f"        \"employer\": {employer!r},\n"
        f"        \"action\": \"reclassify\",\n"
        f"        \"adapter_name\": {detected!r},\n"
        f"        \"ats_family\": {detected!r},\n"
        f"        \"base_url\": {embed_url!r},\n"
        f"        \"hostname\": {hostname!r},\n"
        f"        \"config_blob\": {cb_block},\n"
        f"        \"reason\": \"Source audit 2026-04-24: "
        f"{row['current_adapter']} → {detected} via {signal} (src#{source_id}).\",\n"
        "    },"
    )


def _format_review_stub(employer: str, row: dict[str, Any]) -> str:
    """Emit a commented-out stub for a low-confidence transition."""
    detected = row["detected_adapter"]
    signal = row.get("detection_signal") or "unknown"
    reason = row.get("manual_review_reason") or "confidence=low"
    embed = row.get("detected_embed_url")
    return (
        f"    # ⚠️ MANUAL REVIEW  {employer}\n"
        f"    #   current_adapter={row['current_adapter']}  "
        f"detected_adapter={detected}  signal={signal}\n"
        f"    #   reason: {reason}\n"
        f"    #   base_url:          {row['base_url']}\n"
        f"    #   detected_embed_url: {embed or 'None'}\n"
        f"    #   source_id:         {row.get('source_id')}\n"
        f"    # TODO: hand-derive config_blob, then uncomment.\n"
        "    # {\n"
        f"    #     \"employer\": {employer!r},\n"
        "    #     \"action\": \"reclassify\",\n"
        f"    #     \"adapter_name\": {detected!r},\n"
        f"    #     \"ats_family\": {detected!r},\n"
        "    #     \"base_url\": \"...\",\n"
        "    #     \"hostname\": \"...\",\n"
        "    #     \"config_blob\": { ... },\n"
        "    #     \"reason\": \"...\",\n"
        "    # },"
    )


def run_gen_corrections(args: argparse.Namespace) -> int:
    path = Path(args.output)
    rows = _read_jsonl(path)
    transitions = [r for r in rows if r.get("transition") and r.get("detected_adapter")]
    if not transitions:
        print("No transitions — nothing to generate.")
        return 0

    by_employer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in transitions:
        by_employer[r["employer"]].append(r)

    auto_lines: list[str] = []
    review_lines: list[str] = []
    auto_count = 0
    review_count = 0

    for employer, entries in sorted(by_employer.items()):
        detected_set = {e["detected_adapter"] for e in entries}
        if len(detected_set) != 1:
            review_lines.append(
                f"    # ⚠️ MANUAL REVIEW  {employer}  — rows disagree on "
                f"detected adapter: {sorted(detected_set)}"
            )
            review_count += 1
            continue
        e = entries[0]
        confidence = e.get("confidence", "low")
        cfg = e.get("proposed_config_blob")
        if confidence == "high" and cfg:
            auto_lines.append(_format_correction_dict(employer, e, cfg))
            auto_count += 1
        else:
            review_lines.append(_format_review_stub(employer, e))
            review_count += 1

    auto_header = (
        "# ─── AUTO-APPLICABLE (high-confidence) ─────────────────────────────\n"
        "# Paste the dicts below into scripts/apply_source_corrections.py's\n"
        "# _CORRECTIONS list. Each has been synthesised with a validated\n"
        "# adapter-specific config_blob. Safe to bulk-apply after a quick eyeball.\n"
    )
    review_header = (
        "\n# ─── MANUAL REVIEW (low-confidence) ────────────────────────────────\n"
        "# These transitions lack an extractable tenant URL or validated\n"
        "# config_blob. Each stub is commented out — open the base_url in a\n"
        "# browser, hand-derive the config, then uncomment.\n"
    )
    footer = (
        f"\n# Totals: auto-applicable={auto_count}, manual-review={review_count}, "
        f"transitions={len(transitions)}, employers={len(by_employer)}\n"
    )

    auto_text = "\n".join(auto_lines)
    review_text = "\n".join(review_lines)

    if args.output_auto:
        Path(args.output_auto).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_auto).write_text(auto_header + auto_text + footer + "\n")
        print(f"Wrote {auto_count} auto-applicable entries → {args.output_auto}")
    if args.output_review:
        Path(args.output_review).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_review).write_text(review_header + review_text + footer + "\n")
        print(f"Wrote {review_count} manual-review stubs → {args.output_review}")
    if not (args.output_auto or args.output_review):
        print(auto_header + auto_text)
        if review_lines:
            print(review_header + review_text)
        print(footer.rstrip())
    return 0


# ── CLI wire-up ─────────────────────────────────────────────────────────────

def main() -> int:
    root = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    subparsers = root.add_subparsers(dest="cmd", required=True)

    p_audit = subparsers.add_parser("audit", help="Probe sources, write JSONL")
    p_audit.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL output path.")
    p_audit.add_argument("--limit", type=int, help="Max sources to process (for testing).")
    p_audit.add_argument("--include-inactive", action="store_true", help="Include inactive sources too.")
    p_audit.add_argument("--concurrency", type=int, default=10, help="Concurrent probes (default 10).")
    p_audit.add_argument("--probe-timeout", type=float, default=10.0, help="httpx GET timeout seconds.")
    p_audit.add_argument("--wall-timeout", type=float, default=45.0, help="Per-source overall timeout (longer to accommodate Firefox fallback).")
    p_audit.add_argument("--no-resume", dest="resume", action="store_false", help="Don't skip already-probed rows.")
    p_audit.add_argument(
        "--no-firefox-fallback", dest="firefox_fallback", action="store_false",
        help="Skip Firefox fallback for JS-rendered iframes (httpx-only)."
    )
    p_audit.add_argument(
        "--refresh-embed", action="store_true",
        help="Re-probe rows from older audit runs that lack Phase 2.5 fields."
    )
    p_audit.set_defaults(resume=True, firefox_fallback=True, refresh_embed=False)

    p_summary = subparsers.add_parser("summary", help="Print summary of a JSONL audit file")
    p_summary.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL path to summarise.")

    p_gen = subparsers.add_parser("gen-corrections", help="Emit correction entries from JSONL")
    p_gen.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSONL path to read.")
    p_gen.add_argument(
        "--output-auto", help="Write auto-applicable dicts to this file instead of stdout."
    )
    p_gen.add_argument(
        "--output-review", help="Write manual-review stubs to this file instead of stdout."
    )

    args = root.parse_args()
    if args.cmd == "audit":
        return asyncio.run(run_audit(args))
    if args.cmd == "summary":
        return run_summary(args)
    if args.cmd == "gen-corrections":
        return run_gen_corrections(args)
    root.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
