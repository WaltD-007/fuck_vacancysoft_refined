from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, EnrichedJob, RawJob, Source
from vacancysoft.pipelines.classification import build_classification_payload


def persist_classification_for_enriched_job(session: Session, enriched_job: EnrichedJob) -> ClassificationResult:
    payload = build_classification_payload(enriched_job_id=enriched_job.id, title=enriched_job.title)
    existing_results = list(
        session.execute(
            select(ClassificationResult)
            .where(ClassificationResult.enriched_job_id == enriched_job.id)
            .order_by(ClassificationResult.created_at.desc())
        ).scalars()
    )

    existing = existing_results[0] if existing_results else None
    duplicates = existing_results[1:] if len(existing_results) > 1 else []

    values = {
        "enriched_job_id": enriched_job.id,
        "classifier_version": "rule_classifier_v3",
        "taxonomy_version": payload.taxonomy_version,
        "target_function": payload.primary_taxonomy_key,
        "target_domain": None,
        "primary_taxonomy_key": payload.primary_taxonomy_key,
        "secondary_taxonomy_keys": payload.secondary_taxonomy_keys,
        "sub_specialism": payload.sub_specialism,
        "sub_specialism_confidence": payload.sub_specialism_confidence,
        "employment_type": payload.employment_type,
        "title_relevance_score": payload.title_relevance_score,
        "classification_confidence": payload.classification_confidence,
        "matched_terms": {"title": enriched_job.title},
        "excluded_terms": {},
        "reasons": payload.reasons,
        "decision": payload.decision,
    }

    if existing is None:
        result = ClassificationResult(**values)
        session.add(result)
        session.flush()
        return result

    for key, value in values.items():
        setattr(existing, key, value)

    for duplicate in duplicates:
        session.delete(duplicate)

    session.flush()
    return existing


def classify_enriched_jobs(
    session: Session,
    limit: int | None = None,
    adapter_name: str | None = None,
) -> int:
    """Classify every EnrichedJob that hasn't been classified yet.

    Uses NOT EXISTS (not NOT IN) for the same reason enrich_raw_jobs does
    — Postgres can use the unique index on classification_results.enriched_job_id
    as an anti-semi-join. See the 2026-04-20 investigation notes on the
    pipeline stall.

    When `adapter_name` is set, narrows to EnrichedJobs whose source
    adapter matches — supports `prospero pipeline run --adapter <x>`.
    """
    stmt = (
        select(EnrichedJob)
        .where(
            ~exists().where(ClassificationResult.enriched_job_id == EnrichedJob.id)
        )
        .where(EnrichedJob.detail_fetch_status.notin_(["geo_filtered", "agency_filtered", "title_filtered"]))
    )
    if adapter_name is not None:
        stmt = (
            stmt.join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .join(Source, RawJob.source_id == Source.id)
            .where(Source.adapter_name == adapter_name)
        )
    stmt = stmt.order_by(EnrichedJob.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    jobs = list(session.execute(stmt).scalars())

    count = 0
    for enriched_job in jobs:
        persist_classification_for_enriched_job(session, enriched_job)
        count += 1

    session.commit()
    return count
