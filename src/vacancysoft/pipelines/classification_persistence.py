from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, RawJob
from vacancysoft.pipelines.classification import build_classification_payload


def persist_classification_for_raw_job(session: Session, raw_job: RawJob) -> ClassificationResult:
    payload = build_classification_payload(enriched_job_id=raw_job.id, title=raw_job.title_raw)
    existing = session.execute(
        select(ClassificationResult).where(ClassificationResult.enriched_job_id == raw_job.id)
    ).scalar_one_or_none()

    values = {
        "enriched_job_id": raw_job.id,
        "classifier_version": "demo_classifier_v1",
        "taxonomy_version": payload.taxonomy_version,
        "target_function": payload.primary_taxonomy_key,
        "target_domain": None,
        "primary_taxonomy_key": payload.primary_taxonomy_key,
        "secondary_taxonomy_keys": payload.secondary_taxonomy_keys,
        "title_relevance_score": payload.title_relevance_score,
        "classification_confidence": payload.classification_confidence,
        "matched_terms": {"title": raw_job.title_raw},
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
    session.flush()
    return existing


def classify_raw_jobs(session: Session, limit: int | None = None) -> int:
    stmt = select(RawJob).order_by(RawJob.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    raw_jobs = list(session.execute(stmt).scalars())

    count = 0
    for raw_job in raw_jobs:
        persist_classification_for_raw_job(session, raw_job)
        count += 1

    session.commit()
    return count
