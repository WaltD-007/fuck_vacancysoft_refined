from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, EnrichedJob, ScoreResult
from vacancysoft.scoring.engine import compute_export_score


def _decision_from_score(score: float) -> str:
    if score >= 0.75:
        return "accepted"
    if score >= 0.45:
        return "review"
    return "rejected"


def persist_score_for_enriched_job(session: Session, enriched_job: EnrichedJob) -> ScoreResult | None:
    classification = session.execute(
        select(ClassificationResult).where(ClassificationResult.enriched_job_id == enriched_job.id)
    ).scalar_one_or_none()
    if classification is None:
        return None

    title_relevance = classification.title_relevance_score
    location_confidence = min(enriched_job.enrichment_confidence, 1.0)
    freshness_confidence = 0.85 if enriched_job.posted_at else 0.3
    source_reliability = 0.8
    completeness = enriched_job.completeness_score
    classification_confidence = classification.classification_confidence
    score = compute_export_score(
        title_relevance=title_relevance,
        location_confidence=location_confidence,
        freshness_confidence=freshness_confidence,
        source_reliability=source_reliability,
        completeness=completeness,
        classification_confidence=classification_confidence,
    )
    export_decision = _decision_from_score(score)

    existing = session.execute(
        select(ScoreResult).where(ScoreResult.enriched_job_id == enriched_job.id)
    ).scalar_one_or_none()

    values = {
        "enriched_job_id": enriched_job.id,
        "scoring_version": "scoring_v1",
        "title_relevance_score": title_relevance,
        "location_confidence_score": location_confidence,
        "freshness_confidence_score": freshness_confidence,
        "source_reliability_score": source_reliability,
        "completeness_score": completeness,
        "classification_confidence_score": classification_confidence,
        "export_eligibility_score": score,
        "export_decision": export_decision,
        "reasons": {
            "classification_decision": classification.decision,
            "taxonomy": classification.primary_taxonomy_key,
        },
    }

    if existing is None:
        result = ScoreResult(**values)
        session.add(result)
        session.flush()
        return result

    for key, value in values.items():
        setattr(existing, key, value)
    session.flush()
    return existing


def score_enriched_jobs(session: Session, limit: int | None = None) -> int:
    already_scored = select(ScoreResult.enriched_job_id)
    stmt = (
        select(EnrichedJob)
        .where(~EnrichedJob.id.in_(already_scored))
        .order_by(EnrichedJob.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    jobs = list(session.execute(stmt).scalars())

    count = 0
    for enriched_job in jobs:
        result = persist_score_for_enriched_job(session, enriched_job)
        if result is not None:
            count += 1

    session.commit()
    return count
