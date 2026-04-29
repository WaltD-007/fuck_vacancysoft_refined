"""SQLAlchemy declarative model definitions.

Previously split across `models.py` (31-line re-export shim) and
`models_v2.py` (real definitions); the shim and the v2 suffix were
historical leftovers from a long-finished migration. Consolidated
during Week 5 of the refactor. Every import `from
vacancysoft.db.models import …` keeps working unchanged because the
public path was already via `models.py`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.utcnow()


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    employer_name: Mapped[str] = mapped_column(String(255), index=True)
    board_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_url: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    ats_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter_name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seed_type: Mapped[str] = mapped_column(String(64), default="manual_seed")
    discovery_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    canonical_company_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    config_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capability_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    run_type: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="scheduled")
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    raw_jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    details_fetched: Mapped[int] = mapped_column(Integer, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    http_requests: Mapped[int] = mapped_column(Integer, default=0)
    browser_pages_opened: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnostics_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExtractionAttempt(Base):
    __tablename__ = "extraction_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_run_id: Mapped[str] = mapped_column(ForeignKey("source_runs.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(32), index=True)
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    completeness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_payload_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RawJob(Base):
    __tablename__ = "raw_jobs"
    __table_args__ = (
        UniqueConstraint("source_id", "job_fingerprint", name="uq_raw_job_source_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    source_run_id: Mapped[str] = mapped_column(ForeignKey("source_runs.id"), index=True)
    extraction_attempt_id: Mapped[str] = mapped_column(ForeignKey("extraction_attempts.id"), index=True)
    external_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detail_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_text_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    discovery_ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_deleted_at_source: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at_source_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provenance_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EnrichedJob(Base):
    __tablename__ = "enriched_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    raw_job_id: Mapped[str] = mapped_column(ForeignKey("raw_jobs.id"), unique=True, index=True)
    canonical_job_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_normalised: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_country: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    location_city: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    location_region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    freshness_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    team: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seniority_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_area_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail_fetch_status: Mapped[str] = mapped_column(String(32), default="pending")
    enrichment_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    provenance_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ClassificationResult(Base):
    __tablename__ = "classification_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), index=True)
    classifier_version: Mapped[str] = mapped_column(String(64), index=True)
    taxonomy_version: Mapped[str] = mapped_column(String(64), index=True)
    target_function: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    target_domain: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    primary_taxonomy_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    secondary_taxonomy_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sub_specialism: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sub_specialism_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    employment_type: Mapped[str] = mapped_column(String(32), default="Permanent", server_default="Permanent", index=True)
    title_relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    matched_terms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    excluded_terms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScoreResult(Base):
    __tablename__ = "score_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), unique=True, index=True)
    scoring_version: Mapped[str] = mapped_column(String(64), index=True)
    title_relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    location_confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    freshness_confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_reliability_score: Mapped[float] = mapped_column(Float, default=0.0)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0)
    classification_confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    export_eligibility_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    export_decision: Mapped[str] = mapped_column(String(32), index=True)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SourceHealth(Base):
    __tablename__ = "source_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), unique=True, index=True)
    current_state: Mapped[str] = mapped_column(String(32), index=True)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_partial_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    zero_result_streak: Mapped[int] = mapped_column(Integer, default=0)
    median_jobs_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_jobs_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomaly_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suspected_migration: Mapped[bool] = mapped_column(Boolean, default=False)
    suspected_replacement_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_diagnostics_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ExportRecord(Base):
    __tablename__ = "export_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    export_batch_id: Mapped[str] = mapped_column(String(255), index=True)
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), index=True)
    export_view: Mapped[str] = mapped_column(String(128), index=True)
    destination: Mapped[str] = mapped_column(String(64), index=True)
    eligibility_decision: Mapped[str] = mapped_column(String(32), index=True)
    payload_hash: Mapped[str] = mapped_column(String(255), index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_response_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IntelligenceDossier(Base):
    __tablename__ = "intelligence_dossiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), index=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    category_used: Mapped[str] = mapped_column(String(128), index=True)
    model_used: Mapped[str] = mapped_column(String(128))
    company_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    stated_vs_actual: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    spec_risk: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    candidate_profiles: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    search_booleans: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_score_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    hiring_managers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Per-call breakdown: list of dicts, one per LLM call that contributed
    # to this dossier. Each entry has call, model, tokens_prompt,
    # tokens_completion, tokens_total, cost_usd, latency_ms.
    call_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CampaignOutput(Base):
    __tablename__ = "campaign_outputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dossier_id: Mapped[str] = mapped_column(ForeignKey("intelligence_dossiers.id"), index=True)
    model_used: Mapped[str] = mapped_column(String(128))
    outreach_emails: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Soft-archive flag for the Campaigns tracker. NULL = active (shown
    # by default); set timestamp = archived (hidden unless ?archived=
    # true|all on the list endpoint). Pending sends must be cancelled
    # before archiving is allowed; see /api/campaigns/{id}/archive.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class ReviewQueueItem(Base):
    __tablename__ = "review_queue_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), index=True)
    queue_type: Mapped[str] = mapped_column(String(64), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    reason_code: Mapped[str] = mapped_column(String(128), index=True)
    reason_summary: Mapped[str] = mapped_column(Text)
    evidence_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ── Outreach email (Microsoft Graph) ──────────────────────────────────
# One row per scheduled or completed outbound email; one row per
# Graph-observed reply. See docs/outreach_email.md §2.4 for the full
# data model and §2.5 for the sequence lifecycle.

# Allowed status transitions for SentMessage.status:
#   pending → sent                     (worker succeeded)
#   pending → failed                   (worker tried, Graph error)
#   pending → cancelled_manual         (operator cancelled remaining sequence)
#   pending → cancelled_replied        (reply observed, auto-cancelled)
#   sent    → (terminal)               (no further state changes)
_SENT_MESSAGE_STATUSES = (
    "pending", "sent", "cancelled_manual", "cancelled_replied", "failed",
)


class SentMessage(Base):
    """One row per scheduled or sent outreach email.

    Created in bulk when the operator clicks "Launch Campaign": the
    scheduler inserts 5 rows (one per sequence-index, all status='pending')
    and registers a deferred ARQ job per row. The worker picks up each
    row at its scheduled time, calls GraphClient.send_mail, and
    transitions status → 'sent' or 'failed'.

    Reply polling uses conversation_id (populated post-send) to detect
    replies and cancel any still-pending rows in the same conversation.

    Subject + body are stored at-rest so a post-send audit can confirm
    what went out. In dry-run mode they're still stored — but
    graph_message_id will be the synthetic dryrun-msg-* value.
    """
    __tablename__ = "sent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    campaign_output_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_outputs.id"), index=True
    )
    sender_user_id: Mapped[str] = mapped_column(String(255), index=True)
    recipient_email: Mapped[str] = mapped_column(String(320))
    # Operator-verified hiring-manager name (typed into the Builder
    # alongside the email). Nullable for back-compat with rows written
    # before migration 0014; the Campaigns list/detail endpoints fall
    # back to the dossier's hiring_managers[].name when this is NULL.
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence_index: Mapped[int] = mapped_column(Integer)
    tone: Mapped[str] = mapped_column(String(32))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    graph_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    arq_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ReceivedReply(Base):
    """One row per Graph-observed inbound reply.

    Many-to-one with SentMessage via conversation_id (not via direct
    FK — the reply lands in a conversation that may have multiple
    pending/sent messages in it, and we want to track which sent-message
    it was "replying to" as best-effort via matched_sent_message_id).

    No body or attachment content is stored — Mail.ReadBasic doesn't
    expose them and we don't need them for the cancel-on-reply logic.
    """
    __tablename__ = "received_replies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(255), index=True)
    sender_user_id: Mapped[str] = mapped_column(String(255), index=True)
    graph_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    from_email: Mapped[str] = mapped_column(String(320))
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    matched_sent_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("sent_messages.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Outreach tracking (open + click pixels) ──────────────────────────
# One row per pixel-load (open) or link-click on a sent email. Logged by
# the unauthenticated /t/* endpoints — recipients' mail clients hit
# them as a side-effect of rendering the email.
#
# Both link to SentMessage via FK so aggregates can be computed per
# campaign or per recipient. ip_hash stores HMAC_SHA256(salt, ip) for
# privacy + dedupe; the salt is derived from PROSPERO_TRACKING_SECRET.
#
# Tenancy seam (organization_id) deferred to the tenancy migration —
# when that lands, both tables get the column added alongside every
# other outreach-relevant table.
#
# See docs/prospero_architecture.md §"Outreach tracking" for design.


class OpenEvent(Base):
    """One row per pixel-load on a sent email.

    Deduped at write time within a 60-second window per
    sent_message_id to absorb Outlook preview-pane double counts. The
    deduper is in outreach/tracking.py, not enforced at the DB level
    — we want to log every event we observe and let the writer decide.

    likely_apple_mpp is set when the user-agent matches a known
    pre-fetch pattern (Gmail's GoogleImageProxy, Apple Mail Privacy
    Protection where detectable). Stored as a flag rather than
    dropping the event so aggregations can choose whether to include.
    """
    __tablename__ = "open_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sent_message_id: Mapped[str] = mapped_column(
        ForeignKey("sent_messages.id"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    likely_apple_mpp: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ClickEvent(Base):
    """One row per link-click on a sent email.

    NOT deduped — repeat clicks are a real signal. Corporate scanner
    pre-fetches (Mimecast, Microsoft Safe Links, Proofpoint) get
    likely_scanner=True at write time. Detected via:
      1. clicked_at - sent_at < 120s (humans don't click that fast), OR
      2. user-agent matches a known scanner string
    See outreach/tracking.py for the canonical list.

    original_url is the URL the operator wrote in their email body —
    stored separately from the rewritten /t/c/<token> URL so we know
    where to redirect on click + what to attribute the event to.
    """
    __tablename__ = "click_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    sent_message_id: Mapped[str] = mapped_column(
        ForeignKey("sent_messages.id"), index=True
    )
    original_url: Mapped[str] = mapped_column(Text)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    likely_scanner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Users (operator identity + per-user settings) ────────────────────
# First step of the multi-user story. See docs/outreach_email.md §2.6
# for where this plugs into the eventual Entra Application Access
# Policy. For now:
#   - email is the primary lookup key (UPNs in the 3-5-person team)
#   - entra_object_id is nullable and backfilled when Entra auth lands
#   - preferences is a free-shape JSON bag keyed by page-section
#     (e.g. dashboard_feed) so adding per-page settings doesn't need
#     new columns or migrations.
#
# No FK from other tables to User yet — the users story is intentionally
# non-relational on day one so it can be rolled back without cascade
# concerns (see the plan at .claude/plans/linear-meandering-rossum.md).


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Azure AD / Entra Object ID. Nullable for now because local/dev
    # users don't have one. When Entra auth lands, backfill via
    # `prospero user link-entra <email> <object-id>` (future CLI).
    entra_object_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Per-user settings bag. Top-level keys are page-section identifiers
    # (e.g. "dashboard_feed") and values are opaque to the backend — the
    # frontend owns the shape. PATCH /api/users/me/preferences does a
    # shallow top-level merge: new top-level keys replace whole
    # sub-dicts, existing top-level keys are preserved.
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # Debounced to one write per user per minute by the identity
    # resolver. Nullable because we only write it after the user has
    # actually made a request.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── Location review queue ───────────────────────────────────────────
# Operator-flagged EnrichedJobs where the location looks wrong (e.g.
# "London" on a role the JD body clearly marks as New York). These
# don't get auto-corrected; they collect here for manual review via
# a future /review UI (not in this PR). Set from the Sources page
# "Wrong location" button on each job row.
#
# One row per flag event; a job can accumulate multiple flags if
# multiple operators hit the button or the same operator flags then
# a re-scrape surfaces a new enriched_job for the same URL. The
# review UI dedupes by enriched_job_id when displaying.


class LocationReviewFlag(Base):
    __tablename__ = "location_review_queue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(
        ForeignKey("enriched_jobs.id"), index=True
    )
    flagged_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # Free-text note the operator can optionally add when flagging.
    # Default empty; frontend can prompt in future.
    note: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ── Campaign voice layer ────────────────────────────────────────────
# Per-user, per-tone free-form voice guidance that the campaign prompt
# resolver injects into the LLM prompt when an operator regenerates a
# campaign. Six rows per user max (one per tone). Cold start (no rows)
# means the base template's default tone->source voice guidance is
# used unchanged — output is byte-identical to pre-voice-layer today.
#
# Voice SAMPLES (the last five actually-sent messages per sequence)
# are NOT stored here — they're queried live from ``sent_messages`` by
# the resolver. No samples table. See .claude/plans/linear-meandering-
# rossum.md for the full design.


class UserCampaignPrompt(Base):
    __tablename__ = "user_campaign_prompts"
    __table_args__ = (
        # Caps each user at six rows (one per tone) and makes upserts
        # in the PUT endpoint cheap.
        UniqueConstraint("user_id", "tone", name="uq_user_campaign_prompts_user_tone"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    # One of the six campaign tones: formal / informal / consultative
    # / direct / candidate_spec / technical. No check constraint —
    # the API layer validates against the allowed set so new tones
    # can be introduced without a schema change.
    tone: Mapped[str] = mapped_column(String(32))
    # Free-form voice guidance. Empty string means "fall back to the
    # template's default guidance for this tone" — the resolver
    # treats empty and missing as identical.
    instructions_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ── Voice training samples ──────────────────────────────────────────
# Operator-authored voice samples saved from the Campaign Builder's
# "Save as training sample" button. Lets the voice layer imitate an
# operator's voice before the Graph send flow exists (i.e. before any
# SentMessage rows with status='sent' are being written).
#
# The resolver unions these with SentMessage.status='sent' rows to
# build the per-sequence voice-sample pool. Once real sends start
# accruing, the 5-per-step rolling window naturally pushes training
# rows out (training is the bootstrap; real sends are the authoritative
# voice signal once they exist).


class VoiceTrainingSample(Base):
    __tablename__ = "voice_training_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    # 1–5, matching the campaign sequence indices.
    sequence_index: Mapped[int] = mapped_column(Integer, index=True)
    # One of the six campaign tones: formal / informal / consultative
    # / direct / candidate_spec / technical. API-layer validated;
    # no DB-level CHECK constraint so new tones are schema-free.
    tone: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    # Optional link back to the lead the operator was viewing when
    # they saved the sample — useful for future analytics. No FK
    # cascade: if the enriched_job is later deleted (Dead job), the
    # training sample stays because the voice signal is still useful.
    source_enriched_job_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
