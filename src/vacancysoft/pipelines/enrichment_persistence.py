from __future__ import annotations

from hashlib import sha1

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import EnrichedJob, RawJob
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import normalise_location


def _canonical_job_key(raw_job: RawJob, location: dict) -> str:
    basis = "|".join(
        [
            (raw_job.title_raw or "").strip().lower(),
            (location.get("city") or "").strip().lower(),
            (location.get("country") or "").strip().lower(),
            str(raw_job.source_id),
        ]
    )
    return sha1(basis.encode("utf-8")).hexdigest()


def persist_enrichment_for_raw_job(session: Session, raw_job: RawJob) -> EnrichedJob:
    location = normalise_location(raw_job.location_raw)
    posted_at = parse_posted_date(raw_job.posted_at_raw)
    title = raw_job.title_raw
    title_normalised = title.strip().lower() if title else None
    canonical_job_key = _canonical_job_key(raw_job, location)

    existing = session.execute(
        select(EnrichedJob).where(EnrichedJob.raw_job_id == raw_job.id)
    ).scalar_one_or_none()

    values = {
        "raw_job_id": raw_job.id,
        "canonical_job_key": canonical_job_key,
        "title": title,
        "title_normalised": title_normalised,
        "location_text": raw_job.location_raw,
        "location_country": location.get("country"),
        "location_city": location.get("city"),
        "location_region": location.get("region"),
        "location_type": None,
        "posted_at": posted_at,
        "freshness_bucket": "recent" if posted_at else "unknown",
        "description_text": raw_job.description_raw,
        "team": None,
        "employment_type": None,
        "seniority_hint": None,
        "business_area_hint": None,
        "detail_fetch_status": "demo_enriched",
        "enrichment_confidence": max(location.get("confidence", 0.0), raw_job.extraction_confidence),
        "completeness_score": raw_job.completeness_score,
        "provenance_blob": {
            "raw_job_id": raw_job.id,
            "mode": "demo_enrichment",
        },
    }

    if existing is None:
        enriched = EnrichedJob(**values)
        session.add(enriched)
        session.flush()
        return enriched

    for key, value in values.items():
        setattr(existing, key, value)
    session.flush()
    return existing


def enrich_raw_jobs(session: Session, limit: int | None = None) -> int:
    stmt = select(RawJob).order_by(RawJob.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    raw_jobs = list(session.execute(stmt).scalars())

    count = 0
    for raw_job in raw_jobs:
        persist_enrichment_for_raw_job(session, raw_job)
        count += 1

    session.commit()
    return count
