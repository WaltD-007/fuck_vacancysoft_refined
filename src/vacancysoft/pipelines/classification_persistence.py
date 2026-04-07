from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, EnrichedJob
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
        "classifier_version": "rule_classifier_v2",
        "taxonomy_version": payload.taxonomy_version,
        "target_function": payload.primary_taxonomy_key,
        "target_domain": None,
        "primary_taxonomy_key": payload.primary_taxonomy_key,
        "secondary_taxonomy_keys": payload.secondary_taxonomy_keys,
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


def classify_enriched_jobs(session: Session, limit: int | None = None) -> int:
    already_classified = select(ClassificationResult.enriched_job_id)
    stmt = (
        select(EnrichedJob)
        .where(~EnrichedJob.id.in_(already_classified))
        .order_by(EnrichedJob.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    jobs = list(session.execute(stmt).scalars())

    count = 0
    for enriched_job in jobs:
        persist_classification_for_enriched_job(session, enriched_job)
        count += 1

    session.commit()
    return count
