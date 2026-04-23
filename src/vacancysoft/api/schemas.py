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
      * "exists"   — a direct card already exists; no Coresignal call made. When
                     this is returned by /search the UI may offer an update flow
                     (see `can_update` below).
      * "ok"       — card created and scraped (returned by /confirm only)
    """
    status: str
    jobs_found: int
    company: str
    source_id: int | None = None
    message: str
    candidates: list[AddCompanyCandidate] = []  # populated on /search when status="ready"
    # True when status="exists" AND the direct card is updateable via a CoreSignal
    # sweep (i.e. it's a real active direct card, not a broken stub). The UI uses
    # this to offer an "Update via CoreSignal" action instead of a terminal banner.
    can_update: bool = False


# ── Update-existing-card flow (CoreSignal sweep against an existing direct card) ──

class AddCompanyUpdateRequest(BaseModel):
    """Request shape for both /update-preview and /update-commit.

    `source_id` is the direct (non-aggregator) Source row the user is refreshing.
    """
    source_id: int
    days_back: int = 30


class AddCompanyUpdateLead(BaseModel):
    """One lead surfaced by an update-preview sweep — not persisted."""
    external_id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None
    posted_at: str | None = None
    summary: str | None = None


class AddCompanyUpdatePreviewResponse(BaseModel):
    """Response for /update-preview.

    status values:
      * "ready"     — leads list populated; UI shows them and offers Add
      * "no_jobs"   — CoreSignal returned 0 matching leads
      * "not_found" — source_id did not resolve to an active direct card
      * "error"     — the preview call itself failed
    """
    status: str
    source_id: int
    employer_name: str
    leads_found: int
    leads: list[AddCompanyUpdateLead] = []
    message: str


class AddCompanyUpdateCommitResponse(BaseModel):
    """Response for /update-commit.

    Two CoreSignal sources are created/reused per commit (UK + New York City)
    and scraped independently — each gets its own SourceRun history so the
    two geos can be re-run separately from the sources page later.

    status values:
      * "ok"        — all scrapes ran; `leads_added` is the combined count
      * "not_found" — source_id did not resolve to an active direct card
      * "error"     — at least one scrape failed; `leads_added` still reflects
                      whatever did land, and `message` lists the failures
    """
    status: str
    source_id: int
    employer_name: str
    coresignal_source_ids: list[int] = []
    leads_added: int = 0
    message: str


class StatsOut(BaseModel):
    total_sources: int
    active_sources: int
    total_jobs: int
    total_enriched: int
    total_scored: int
    adapters: dict[str, int]
    categories: dict[str, int]


class ScoredJobOut(BaseModel):
    # `id` is the enriched_job_id — passed through so the Sources page
    # drawer's admin buttons (Dead job / Wrong location) can reference
    # the row server-side. It's safe to expose; it's an opaque UUID.
    id: str
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
    """Request for POST /api/leads/paste — text-paste flow.

    The operator pastes the advert body; an LLM extracts
    title / company / location / posted_date from it. No URL is
    accepted — every paste produces a fresh RawJob with
    ``discovered_url = NULL``.
    """
    advert_text: str


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


# ── Users (see alembic/versions/0009_add_users_table.py) ─────────────


class UserOut(BaseModel):
    """User as returned by GET /api/users/me and GET /api/users."""

    id: str
    email: str
    display_name: str
    role: str
    active: bool
    entra_object_id: str | None = None
    preferences: dict
    # last_seen_at + created_at / updated_at deliberately omitted from
    # the client payload — not useful for the UI and adding them risks
    # confusing the optimistic SWR cache in useCurrentUser.

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    """POST /api/users body — admin bootstrap only."""

    email: str
    display_name: str
    entra_object_id: str | None = None
    role: str = "operator"


# PATCH /api/users/me/preferences body is a raw dict — FastAPI binds
# ``patch: dict`` directly on the handler. No Pydantic schema needed
# because the shape is frontend-owned: every top-level key is a
# page-section identifier (dashboard_feed, leads_page, …) with an
# opaque JSON object. Validation lives in the handler.


# ── Voice layer (see alembic/versions/0011_add_user_campaign_prompts.py) ──


class UserCampaignPromptsOut(BaseModel):
    """GET /api/users/me/campaign-prompts response.

    Always returns all six tone keys. Missing DB rows render as
    empty strings — the resolver treats empty and missing as
    identical (both fall back to the base template's default
    guidance for that tone).
    """

    formal: str = ""
    informal: str = ""
    consultative: str = ""
    direct: str = ""
    candidate_spec: str = ""
    technical: str = ""


class VoiceSampleOut(BaseModel):
    """One row from /api/users/me/voice-samples.

    Deliberately thin — no send_at, no enriched_job_id, no
    graph_message_id. The operator-facing audit view only needs
    enough detail to recognise the email.
    """

    subject: str
    body: str
    tone: str


# PUT /api/users/me/campaign-prompts body is a raw dict — FastAPI binds
# ``payload: dict`` on the handler. Shape is operator-owned ({tone: text})
# and validation (allowed tone keys) lives in the handler. Empty string
# means "clear this tone"; missing key means "leave this tone alone".
