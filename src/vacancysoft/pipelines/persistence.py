from __future__ import annotations

from datetime import datetime
from hashlib import sha1
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.adapters.base import DiscoveredJobRecord
from vacancysoft.db.models import ExtractionAttempt, RawJob, Source, SourceRun
from vacancysoft.enrichers.location_normaliser import is_allowed_country, normalise_location


def _job_fingerprint(source_id: int, record: DiscoveredJobRecord) -> str:
    basis = "|".join(
        [
            str(source_id),
            record.external_job_id or "",
            (record.discovered_url or "").strip().lower(),
            (record.title_raw or "").strip().lower(),
            (record.location_raw or "").strip().lower(),
        ]
    )
    return sha1(basis.encode("utf-8")).hexdigest()


def start_source_run(session: Session, source: Source, trigger: str = "manual") -> SourceRun:
    run = SourceRun(source_id=source.id, run_type="discovery", status="running", trigger=trigger)
    session.add(run)
    session.flush()
    return run


def create_extraction_attempt(
    session: Session,
    source: Source,
    source_run: SourceRun,
    method: str = "site_rescue",
    endpoint_url: str | None = None,
) -> ExtractionAttempt:
    attempt = ExtractionAttempt(
        source_run_id=source_run.id,
        source_id=source.id,
        stage="discover",
        method=method,
        endpoint_url=endpoint_url or source.base_url,
        success=True,
        diagnostics_blob={"mode": "demo_discovery"},
    )
    session.add(attempt)
    session.flush()
    return attempt


def upsert_raw_job(
    session: Session,
    source: Source,
    source_run: SourceRun,
    extraction_attempt: ExtractionAttempt,
    record: DiscoveredJobRecord,
) -> RawJob:
    fingerprint = _job_fingerprint(source.id, record)
    existing = session.execute(
        select(RawJob).where(
            RawJob.source_id == source.id,
            RawJob.job_fingerprint == fingerprint,
        )
    ).scalar_one_or_none()

    payload = {
        "source_id": source.id,
        "source_run_id": source_run.id,
        "extraction_attempt_id": extraction_attempt.id,
        "external_job_id": record.external_job_id,
        "canonical_url": record.discovered_url,
        "discovered_url": record.discovered_url,
        "apply_url": record.apply_url,
        "title_raw": record.title_raw,
        "location_raw": record.location_raw,
        "posted_at_raw": record.posted_at_raw,
        "description_raw": record.summary_raw,
        "listing_payload": record.listing_payload,
        "job_fingerprint": fingerprint,
        "completeness_score": record.completeness_score,
        "extraction_confidence": record.extraction_confidence,
        "provenance_blob": record.provenance,
        "last_seen_at": datetime.utcnow(),
    }

    if existing is None:
        raw_job = RawJob(**payload, first_seen_at=datetime.utcnow(), discovery_ts=datetime.utcnow())
        session.add(raw_job)
        session.flush()
        return raw_job

    for key, value in payload.items():
        setattr(existing, key, value)
    session.flush()
    return existing


def finalise_source_run(session: Session, source_run: SourceRun, records_seen: int, raw_jobs_created: int) -> None:
    source_run.records_seen = records_seen
    source_run.raw_jobs_created = raw_jobs_created
    source_run.status = "success"
    source_run.finished_at = datetime.utcnow()
    session.flush()


def _record_in_target_geo(record: DiscoveredJobRecord) -> bool:
    """Drop records whose location resolves to a country outside the allow list.

    Records with unresolved locations (no parseable country) are kept so that
    enrichment can try to resolve them; ``is_allowed_country`` returns True for
    None. Known-but-non-target countries (e.g. "Poland") are dropped here.
    """
    return is_allowed_country(normalise_location(record.location_raw).get("country"))


def persist_discovery_failure(
    session: Session,
    source: Source,
    exc: BaseException,
    trigger: str = "manual",
) -> SourceRun:
    """Persist a failed discovery attempt as a SourceRun + ExtractionAttempt.

    Mirrors persist_discovery_batch's shape but for the failure path.
    Without this, source-level exceptions raised by the adapter (e.g.
    `ValueError: Lever source_config requires slug`, `httpx.ReadTimeout`)
    only get printed to stdout and leave no DB trace — so operators
    running `prospero db stats` or querying `source_runs WHERE status='error'`
    after the fact can't tell which sources failed.

    Added 2026-04-20 after the Lever pipeline run surfaced that the first
    4 of 113 sources failed without persisting anything.
    """
    source_run = SourceRun(
        source_id=source.id,
        run_type="discovery",
        status="error",
        trigger=trigger,
        errors_count=1,
        finished_at=datetime.utcnow(),
        diagnostics_blob={
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:2000],  # cap to keep the JSON small
            "error": f"{type(exc).__name__}: {exc}",
        },
    )
    session.add(source_run)
    session.flush()

    attempt = ExtractionAttempt(
        source_run_id=source_run.id,
        source_id=source.id,
        stage="discover",
        method="site_rescue",
        endpoint_url=source.base_url,
        success=False,
        error_type=type(exc).__name__,
        error_message=str(exc)[:2000],
        diagnostics_blob={"trigger": trigger},
    )
    session.add(attempt)
    session.flush()
    session.commit()
    return source_run


def persist_discovery_batch(
    session: Session,
    source: Source,
    records: Iterable[DiscoveredJobRecord],
    trigger: str = "manual",
) -> tuple[SourceRun, int]:
    items = list(records)
    kept = [r for r in items if _record_in_target_geo(r)]

    source_run = start_source_run(session=session, source=source, trigger=trigger)
    attempt = create_extraction_attempt(session=session, source=source, source_run=source_run)

    count = 0
    for record in kept:
        upsert_raw_job(
            session=session,
            source=source,
            source_run=source_run,
            extraction_attempt=attempt,
            record=record,
        )
        count += 1

    finalise_source_run(session=session, source_run=source_run, records_seen=len(items), raw_jobs_created=count)
    session.commit()
    return source_run, count
