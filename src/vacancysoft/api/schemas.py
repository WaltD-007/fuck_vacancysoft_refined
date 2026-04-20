"""Pydantic request / response models for the Prospero API.

Every `BaseModel` used by a FastAPI handler lives here so the route
modules under `api/routes/` import their schemas from one canonical
place. Extracted verbatim from `api/server.py` during the Week 4 split.
"""

from __future__ import annotations

from pydantic import BaseModel


class SourceOut(BaseModel):
    id: int
    employer_name: str
    adapter_name: str
    base_url: str
    active: bool
    seed_type: str
    ats_family: str | None
    jobs: int = 0
    enriched: int = 0
    scored: int = 0
    categories: dict[str, int] = {}
    categories_by_country: dict[str, dict[str, int]] = {}
    sub_specialisms: dict[str, dict[str, int]] = {}  # {category_label: {sub_specialism: count}}
    sub_specialisms_by_country: dict[str, dict[str, dict[str, int]]] = {}  # {country: {category_label: {sub_specialism: count}}}
    aggregator_hits: dict[str, int] = {}  # {adapter_name: count} for aggregator-contributed rows
    employment_types: dict[str, int] = {}  # {Permanent|Contract: count}
    last_run_status: str | None = None
    last_run_error: str | None = None

    class Config:
        from_attributes = True


class DetectRequest(BaseModel):
    url: str


class DetectResponse(BaseModel):
    adapter: str
    slug: str | None
    url: str
    company_guess: str
    reachable: bool
    job_count: int | None
    error: str | None


class AddSourceRequest(BaseModel):
    url: str
    company: str


class AddSourceResponse(BaseModel):
    id: int
    employer_name: str
    adapter_name: str
    base_url: str
    message: str


class AddCompanyRequest(BaseModel):
    company: str
    countries: list[str] | None = None  # defaults to ["United Kingdom"]
    days_back: int = 30
    employer_exact: str | None = None  # set by /confirm when user picks a specific candidate from the list


class AddCompanyCandidate(BaseModel):
    """One employer name that matches the fuzzy query, with jobs_count within the
    date/country window. Returned by /search so the UI can disambiguate."""
    employer_name: str
    jobs_count: int
    sample_title: str | None = None
    sample_location: str | None = None
    already_in_db: bool = False  # True if this exact employer already has a direct Source row


class AddCompanyResponse(BaseModel):
    """Response for BOTH /search (preview) and /confirm (commit).

    status values:
      * "ready"    — search found jobs, user can now confirm (returned by /search only)
      * "no_jobs"  — nothing to add (returned by /search only)
      * "exists"   — a direct card already exists; no Coresignal call made
      * "ok"       — card created and scraped (returned by /confirm only)
    """
    status: str
    jobs_found: int
    company: str
    source_id: int | None = None
    message: str
    candidates: list[AddCompanyCandidate] = []  # populated on /search when status="ready"


class StatsOut(BaseModel):
    total_sources: int
    active_sources: int
    total_jobs: int
    total_enriched: int
    total_scored: int
    adapters: dict[str, int]
    categories: dict[str, int]


class ScoredJobOut(BaseModel):
    title: str
    company: str
    location: str | None
    country: str | None
    category: str | None
    sub_specialism: str | None
    score: float | None
    url: str | None

    class Config:
        from_attributes = True


class PasteLeadRequest(BaseModel):
    """Single-field request for POST /api/leads/paste.

    Title / company / location come from the Playwright runner's structured
    metadata extraction; the operator only supplies the URL.
    """
    url: str


class QueueRequest(BaseModel):
    title: str
    company: str
    location: str | None = None
    country: str | None = None
    category: str | None = None
    sub_specialism: str | None = None
    url: str | None = None
    score: float | None = None
    board_url: str | None = None


class ScrapeResponse(BaseModel):
    source_key: str
    employer_name: str
    jobs_found: int
    status: str
    removed: bool = False


class MarkAgencyRequest(BaseModel):
    company: str


class MarkAgencyResponse(BaseModel):
    added: bool
    deleted_jobs: int
    deleted_classifications: int
    deleted_scores: int
    deleted_dossiers: int
    deleted_queue_items: int


def dossier_to_dict(d) -> dict:
    """Serialise an IntelligenceDossier ORM row to the dict shape the UI
    consumes. Shared between the campaigns route (on-demand generation)
    and the leads route (inline-with-queue projection) so both paths
    return identical payloads."""
    return {
        "id": d.id,
        "category": d.category_used,
        "model": d.model_used,
        "tokens": d.tokens_used,
        "tokens_prompt": d.tokens_prompt,
        "tokens_completion": d.tokens_completion,
        "cost_usd": d.cost_usd,
        "call_breakdown": d.call_breakdown or [],
        "latency_ms": d.latency_ms,
        "lead_score": d.lead_score,
        "lead_score_justification": d.lead_score_justification,
        "company_context": d.company_context,
        "core_problem": d.core_problem,
        "stated_vs_actual": d.stated_vs_actual or [],
        "spec_risk": d.spec_risk or [],
        "candidate_profiles": d.candidate_profiles or [],
        "search_booleans": d.search_booleans or {},
        "hiring_managers": d.hiring_managers or [],
    }
