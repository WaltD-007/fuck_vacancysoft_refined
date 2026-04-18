from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from vacancysoft.db.models import ClassificationResult, EnrichedJob, ExportRecord, RawJob, ScoreResult, Source


def _base_export_query():
    # Prefer the real employer extracted from the listing payload (stored in
    # EnrichedJob.team) over the Source-level employer_name, which is just
    # the aggregator name for Adzuna/Reed/Google Jobs.
    employer_col = case(
        (EnrichedJob.team.isnot(None), EnrichedJob.team),
        else_=Source.employer_name,
    ).label("employer_name")

    return (
        select(
            EnrichedJob.id.label("enriched_job_id"),
            EnrichedJob.title,
            EnrichedJob.location_text,
            EnrichedJob.location_city,
            EnrichedJob.location_country,
            EnrichedJob.posted_at,
            ClassificationResult.primary_taxonomy_key,
            ClassificationResult.secondary_taxonomy_keys,
            ClassificationResult.taxonomy_version,
            ClassificationResult.classifier_version,
            ClassificationResult.decision.label("classification_decision"),
            ScoreResult.export_decision,
            ScoreResult.export_eligibility_score,
            ScoreResult.scoring_version,
            employer_col,
            Source.source_key,
            RawJob.discovered_url,
            RawJob.apply_url,
        )
        .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
        .join(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id)
        .join(RawJob, RawJob.id == EnrichedJob.raw_job_id)
        .join(Source, Source.id == RawJob.source_id)
    )


def accepted_only_query():
    return _base_export_query().where(ScoreResult.export_decision == "accepted")


def accepted_plus_review_query():
    return _base_export_query().where(ScoreResult.export_decision.in_(["accepted", "review"]))


def new_leads_only_query(destination: str = "webhook"):
    """Return accepted+review leads that have NOT been exported to the given destination."""
    already_exported = (
        select(ExportRecord.enriched_job_id)
        .where(ExportRecord.destination == destination)
        .where(ExportRecord.delivered.is_(True))
    )
    return (
        _base_export_query()
        .where(ScoreResult.export_decision.in_(["accepted", "review"]))
        .where(~EnrichedJob.id.in_(already_exported))
    )


def grouped_by_taxonomy_query():
    return (
        select(
            ClassificationResult.primary_taxonomy_key,
            ScoreResult.export_decision,
            func.count().label("job_count"),
        )
        .join(ScoreResult, ScoreResult.enriched_job_id == ClassificationResult.enriched_job_id)
        .group_by(ClassificationResult.primary_taxonomy_key, ScoreResult.export_decision)
        .order_by(ClassificationResult.primary_taxonomy_key.asc(), ScoreResult.export_decision.asc())
    )


def load_exporter_config(path: str | Path = "configs/exporters.toml") -> dict[str, Any]:
    with Path(path).open("rb") as fh:
        return tomllib.load(fh)


def client_segment_query(segment_name: str, config: dict[str, Any]):
    segment = config.get("client_segments", {}).get(segment_name)
    if segment is None:
        raise KeyError(f"Unknown client segment: {segment_name}")

    query = _base_export_query()
    taxonomy_keys = segment.get("taxonomy_keys") or []
    include_export_decisions = segment.get("include_export_decisions") or []

    if taxonomy_keys:
        query = query.where(ClassificationResult.primary_taxonomy_key.in_(taxonomy_keys))
    if include_export_decisions:
        query = query.where(ScoreResult.export_decision.in_(include_export_decisions))
    return query


def fetch_rows(session: Session, stmt, limit: int = 100) -> list[Any]:
    return list(session.execute(stmt.limit(limit)))
