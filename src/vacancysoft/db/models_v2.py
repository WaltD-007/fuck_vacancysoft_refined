from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BaseV2(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.utcnow()


class Source(BaseV2):
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


class SourceRun(BaseV2):
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


class ExtractionAttempt(BaseV2):
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


class RawJob(BaseV2):
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
    provenance_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EnrichedJob(BaseV2):
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


class ClassificationResult(BaseV2):
    __tablename__ = "classification_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enriched_job_id: Mapped[str] = mapped_column(ForeignKey("enriched_jobs.id"), index=True)
    classifier_version: Mapped[str] = mapped_column(String(64), index=True)
    taxonomy_version: Mapped[str] = mapped_column(String(64), index=True)
    target_function: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    target_domain: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    primary_taxonomy_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    secondary_taxonomy_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    employment_type: Mapped[str] = mapped_column(String(32), default="Permanent", server_default="Permanent", index=True)
    title_relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    matched_terms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    excluded_terms: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScoreResult(BaseV2):
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


class SourceHealth(BaseV2):
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


class ExportRecord(BaseV2):
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


class IntelligenceDossier(BaseV2):
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


class CampaignOutput(BaseV2):
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


class ReviewQueueItem(BaseV2):
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
