"""Multi-aggregator preview for the Add Company flow.

Replaces the single-CoreSignal preview that shipped with the Add
Company wizard. Operator clicks an existing direct card; we fan out
to four aggregators in parallel — CoreSignal (native company filter),
Adzuna (loose company query), eFinancialCareers and Google Jobs
(keyword search by employer name) — collect every lead, post-filter
by token-subset employer match, dedup by URL across sources, and
return one unified list with each row tagged by which aggregator
surfaced it.

Reed is intentionally absent (operator preference 2026-04-30 — too
much noise for the cost). Add it back via _DISPATCH if that changes.

Failure model
-------------
Each per-aggregator call is independently try/except'd. One source
failing (SerpAPI quota, Adzuna 5xx, eFC timeout) doesn't kill the
others — its name appears in `aggregators_errored` so the modal can
surface a small "X unreachable" note above the lead list.

Cost
----
- CoreSignal: existing paid call, unchanged.
- Adzuna / eFC: free tiers cover normal use.
- Google Jobs (SerpAPI): ~$0.005-0.01 per Add Company click.

Token-subset post-filter
------------------------
Adzuna's `&company=` is loose; eFC and Google's keyword searches are
unscoped. So we apply the same token-subset rule that ``is_recruiter``
uses (PR #97/#105) — every alphanumeric token of the queried employer
must be a subset of the candidate row's tokens. So "Goldman Sachs"
matches "Goldman Sachs International Bank" but not "JP Morgan" or a
recruiter mentioning Goldman in the JD.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Adapter keys we fan out to. Order matters for the dedup tie-break:
# whichever source surfaces a URL first "wins" the row, so put the
# higher-quality / cheaper sources first.
_DISPATCH = ("coresignal", "adzuna", "efinancialcareers", "google_jobs")


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str | None) -> set[str]:
    if not s:
        return set()
    return set(_TOKEN_RE.findall(s.lower()))


def _company_token_match(query_employer: str, candidate_company: str | None) -> bool:
    """True iff every alphanumeric token of the queried employer is
    present in the candidate company's tokens. Same rule the runtime
    agency-exclusions matcher uses (recruiter_filter.is_recruiter).

    'Goldman Sachs' → matches 'Goldman Sachs International Bank',
    'Goldman Sachs Asset Management', 'goldman-sachs uk ltd'.
    Doesn't match 'JP Morgan' or recruiters with 'Goldman' only in
    the description (because we test against company, not title/desc).
    """
    q = _tokens(query_employer)
    c = _tokens(candidate_company)
    return bool(q) and q.issubset(c)


@dataclass
class PreviewLead:
    """One row in the unified preview list. Mirrors the fields the
    schemas.AddCompanyUpdateLead model exposes, plus source_adapter."""
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str | None
    posted_at: str | None
    summary: str | None
    source_adapter: str


@dataclass
class AggregatorPreviewResult:
    leads: list[PreviewLead] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)
    attempted: int = 0


# ── Per-adapter shims ─────────────────────────────────────────────────


def _best_url(payload: dict | None, fallback: str | None) -> str | None:
    """Lightweight URL picker — mirrors api/routes/add_company._best_lead_url
    but local to avoid pulling the route module in here."""
    if isinstance(payload, dict):
        for field_name in ("external_url", "apply_url", "url", "job_url", "source_url", "redirect_url"):
            val = payload.get(field_name)
            if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
                return val.strip()
    if isinstance(fallback, str) and fallback.strip().startswith(("http://", "https://")):
        return fallback.strip()
    return None


def _record_to_lead(j: Any, source_adapter: str) -> PreviewLead:
    """Translate a DiscoveredJobRecord into a PreviewLead. Used by the
    keyword-search adapters where the adapter contract is the source
    of truth for field shape."""
    summary = getattr(j, "summary_raw", None)
    return PreviewLead(
        external_id=getattr(j, "external_job_id", None) or "",
        title=getattr(j, "title_raw", "") or "",
        company=(getattr(j, "provenance", None) or {}).get("company"),
        location=getattr(j, "location_raw", None),
        url=_best_url(
            getattr(j, "listing_payload", None),
            getattr(j, "apply_url", None) or getattr(j, "discovered_url", None),
        ),
        posted_at=getattr(j, "posted_at_raw", None),
        summary=summary[:300] if isinstance(summary, str) else None,
        source_adapter=source_adapter,
    )


async def _fetch_coresignal(employer: str, since: datetime) -> list[PreviewLead]:
    api_key = (os.getenv("CORESIGNAL_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("CORESIGNAL_API_KEY not configured")
    from vacancysoft.adapters.coresignal import CoresignalAdapter
    adapter = CoresignalAdapter()
    days_back = max((datetime.now(timezone.utc) - since).days, 1)
    config = {
        "api_key": api_key,
        "company_filter": employer,
        "use_full_taxonomy": True,
        "use_preview": True,
        "max_age_days": days_back,
    }
    page = await adapter.discover(config, since=since)
    return [_record_to_lead(j, "coresignal") for j in (page.jobs or [])]


async def _fetch_adzuna(employer: str, since: datetime) -> list[PreviewLead]:
    if not (os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not configured")
    from vacancysoft.adapters.adzuna import AdzunaAdapter
    adapter = AdzunaAdapter()
    config = {
        # Generic high-recall keyword sweep — Adzuna's company= filter
        # then narrows to the employer. The post-filter we run after
        # this call ensures we don't surface jobs that loosely matched.
        "search_terms": ["risk", "compliance", "audit", "quant", "cyber", "legal"],
        "company_filter": employer,
        "countries": ["gb", "us"],
        # Tight cap — preview should be quick.
        "max_pages": 2,
        "results_per_page": 50,
    }
    page = await adapter.discover(config, since=since)
    return [_record_to_lead(j, "adzuna") for j in (page.jobs or [])]


async def _fetch_efinancialcareers(employer: str, since: datetime) -> list[PreviewLead]:
    from vacancysoft.adapters.efinancialcareers import EFinancialCareersAdapter
    adapter = EFinancialCareersAdapter()
    config = {
        # eFC has no native company filter — keyword-search the
        # employer name and rely on the post-filter to keep only the
        # rows where company actually matches.
        "search_terms": [employer],
        "page_size": 50,
        "max_pages": 2,
    }
    page = await adapter.discover(config, since=since)
    return [_record_to_lead(j, "efinancialcareers") for j in (page.jobs or [])]


async def _fetch_google_jobs(employer: str, since: datetime) -> list[PreviewLead]:
    if not os.getenv("SERPAPI_KEY"):
        raise RuntimeError("SERPAPI_KEY not configured")
    from vacancysoft.adapters.google_jobs import GoogleJobsAdapter
    adapter = GoogleJobsAdapter()
    config = {
        "search_terms": [f'"{employer}" jobs'],
        "max_pages": 1,
    }
    page = await adapter.discover(config, since=since)
    return [_record_to_lead(j, "google_jobs") for j in (page.jobs or [])]


_FETCHERS = {
    "coresignal": _fetch_coresignal,
    "adzuna": _fetch_adzuna,
    "efinancialcareers": _fetch_efinancialcareers,
    "google_jobs": _fetch_google_jobs,
}


# ── Top-level fan-out ─────────────────────────────────────────────────


async def fetch_company_leads(
    employer: str,
    days_back: int,
) -> AggregatorPreviewResult:
    """Fan out to every aggregator in _DISPATCH in parallel. Returns
    a unified list of leads (post-filtered by token-subset employer
    match, deduped by URL across sources) plus the names of any
    aggregators that errored."""
    employer = (employer or "").strip()
    if not employer:
        return AggregatorPreviewResult(attempted=0)

    days_back = max(int(days_back), 1)
    since = datetime.now(timezone.utc) - timedelta(days=days_back)

    async def _safe(name: str):
        try:
            return name, await _FETCHERS[name](employer, since), None
        except Exception as exc:  # noqa: BLE001
            logger.warning("aggregator preview failed for %s/%s: %s", name, employer, exc)
            return name, [], exc

    raw = await asyncio.gather(*(_safe(name) for name in _DISPATCH))

    result = AggregatorPreviewResult(attempted=len(_DISPATCH))
    seen_urls: set[str] = set()
    for name, leads, err in raw:
        if err is not None:
            result.errored.append(name)
            continue
        for lead in leads:
            # Token-subset post-filter. CoreSignal already scoped the
            # query by company, so its rows are trusted; the others
            # are post-filtered to weed out broad-keyword false-positives.
            if name != "coresignal" and not _company_token_match(employer, lead.company):
                continue
            url = (lead.url or "").strip()
            if url:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
            result.leads.append(lead)
    return result
