from __future__ import annotations

import tomllib
from datetime import datetime
from functools import lru_cache
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vacancysoft.adapters.base import DiscoveredJobRecord
from vacancysoft.db.models import ExtractionAttempt, RawJob, Source, SourceRun
from vacancysoft.enrichers.location_normaliser import is_allowed_country, normalise_location


@lru_cache(maxsize=1)
def _load_pipeline_config() -> dict[str, Any]:
    """Read ``[pipeline]`` from configs/app.toml. Cached for the process.

    Returns an empty dict if the file or section is missing — all callers
    use ``.get(..., default)`` so they're tolerant of either case.
    """
    path = Path("configs/app.toml")
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("pipeline", {}) or {}
    except Exception:
        return {}


def _capabilities_for_adapter(adapter_name: str | None):
    """Resolve an adapter's AdapterCapabilities; returns None if unknown.

    Lazy-imports ``ADAPTER_REGISTRY`` from ``vacancysoft.adapters`` to
    avoid a circular import at module-load time.
    """
    if not adapter_name:
        return None
    try:
        from vacancysoft.adapters import ADAPTER_REGISTRY
        adapter_cls = ADAPTER_REGISTRY.get(adapter_name)
        return getattr(adapter_cls, "capabilities", None) if adapter_cls else None
    except Exception:
        return None


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
        # Re-discovery clears the dead flag — if a previously-marked-dead
        # job is found again on a fresh scrape, treat it as live again.
        # Operator-set ``is_deleted_at_source`` (via the UI "Dead" button)
        # gets cleared by the same logic since we can't distinguish
        # operator-set from sweep-set today; if that becomes a problem,
        # add a separate ``deleted_by`` column.
        "is_deleted_at_source": False,
        "deleted_at_source_at": None,
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

    # Auto-mark-dead end-of-run sweep. Gated behind a feature flag and
    # adapter capability; safe-by-default.
    sweep_outcome = _maybe_sweep_dead_jobs(session, source_run, records_seen)
    if sweep_outcome is not None:
        diagnostics = dict(source_run.diagnostics_blob or {})
        diagnostics.update(sweep_outcome)
        source_run.diagnostics_blob = diagnostics
        session.flush()


def _maybe_sweep_dead_jobs(
    session: Session,
    source_run: SourceRun,
    records_seen: int,
) -> dict[str, Any] | None:
    """Mark RawJobs not seen in this run as ``is_deleted_at_source=True``.

    Triggered only when:
      1. ``configs/app.toml [pipeline] auto_mark_dead_enabled = true``
      2. Source run completed with status ``success``
      3. ``records_seen > 0`` (a zero-record run is always suspicious —
         could be transient anti-bot, partial fetch, etc.)
      4. The adapter declares ``capabilities.complete_coverage_per_run``
      5. ``records_seen`` is at least
         ``threshold * last_successful_run.records_seen`` (default 75%).
         Protects against a partial-fetch run that returns dramatically
         fewer rows than usual; we'd otherwise mark every "missing" job
         dead based on a transient hiccup.

    Returns a dict to merge into ``source_run.diagnostics_blob`` (with
    keys ``sweep_*`` / ``marked_dead``) describing the sweep outcome,
    or ``None`` if the sweep didn't run for a reason that doesn't merit
    diagnostic recording (e.g. feature flag off).
    """
    config = _load_pipeline_config()
    if not bool(config.get("auto_mark_dead_enabled", False)):
        return None
    if source_run.status != "success":
        return {"sweep_skipped": True, "sweep_skip_reason": f"status={source_run.status}"}
    if records_seen <= 0:
        return {"sweep_skipped": True, "sweep_skip_reason": "records_seen=0"}

    source = session.get(Source, source_run.source_id)
    if source is None:
        return {"sweep_skipped": True, "sweep_skip_reason": "source_missing"}

    capabilities = _capabilities_for_adapter(source.adapter_name)
    if capabilities is None or not getattr(capabilities, "complete_coverage_per_run", False):
        return {
            "sweep_skipped": True,
            "sweep_skip_reason": f"adapter={source.adapter_name} not opted-in",
        }

    threshold = float(config.get("auto_mark_dead_threshold", 0.75))
    last_count = _last_successful_records_seen(session, source.id, exclude_run_id=source_run.id)
    if last_count is not None and last_count > 0:
        if records_seen < threshold * last_count:
            return {
                "sweep_skipped": True,
                "sweep_skip_reason": (
                    f"records_seen={records_seen} < {threshold} * "
                    f"last_successful={last_count}"
                ),
                "sweep_threshold": threshold,
                "sweep_last_count": last_count,
            }

    marked = _execute_sweep(session, source.id, source_run.started_at)
    return {
        "marked_dead": marked,
        "sweep_threshold": threshold,
        "sweep_last_count": last_count,
    }


def _last_successful_records_seen(
    session: Session,
    source_id: int,
    exclude_run_id: str,
) -> int | None:
    """Return the records_seen count of the most recent prior successful run.

    Excludes the run currently in progress (``exclude_run_id``) so we
    compare against history, not ourselves. Returns ``None`` for
    first-ever runs (no prior history → no threshold check applied).
    """
    stmt = (
        select(SourceRun.records_seen)
        .where(SourceRun.source_id == source_id)
        .where(SourceRun.status == "success")
        .where(SourceRun.id != exclude_run_id)
        .order_by(SourceRun.started_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _execute_sweep(session: Session, source_id: int, run_started_at: datetime) -> int:
    """UPDATE raw_jobs to set is_deleted_at_source=True on unseen rows.

    "Unseen this run" = ``last_seen_at < run_started_at``. The upsert
    inside this run refreshes ``last_seen_at`` to the upsert moment
    (>= run_started_at), so the inequality cleanly excludes anything
    we touched.
    """
    now = datetime.utcnow()
    stmt = (
        update(RawJob)
        .where(RawJob.source_id == source_id)
        .where(RawJob.last_seen_at < run_started_at)
        .where(RawJob.is_deleted_at_source.is_(False))
        .values(is_deleted_at_source=True, deleted_at_source_at=now)
    )
    result = session.execute(stmt)
    return int(result.rowcount or 0)


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
