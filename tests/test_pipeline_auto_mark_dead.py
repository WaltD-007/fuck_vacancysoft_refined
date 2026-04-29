"""Tests for the auto-mark-dead end-of-run sweep.

Covers ``pipelines/persistence.py::_maybe_sweep_dead_jobs`` and the
flag-reset on rediscovery in ``upsert_raw_job``.

Test matrix:
  - happy path: jobs not seen this run get marked dead
  - safety: skip when below the 75% threshold
  - safety: skip on zero-record runs
  - safety: skip for non-opted-in adapters (aggregators)
  - safety: skip when run failed
  - safety: skip when feature flag is off
  - first-run: no prior history → sweep still runs (records_seen > 0)
  - rediscovery: re-seeing a dead-marked job clears the flag
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vacancysoft.adapters.base import DiscoveredJobRecord
from vacancysoft.db.models import (
    Base,
    ExtractionAttempt,
    RawJob,
    Source,
    SourceRun,
)
from vacancysoft.pipelines import persistence


@pytest.fixture()
def session():
    """Fresh in-memory SQLite per test with the full schema."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


@pytest.fixture(autouse=True)
def clear_pipeline_config_cache():
    """The @lru_cache on _load_pipeline_config persists across tests
    by default; clear it so per-test monkeypatching takes effect."""
    persistence._load_pipeline_config.cache_clear()
    yield
    persistence._load_pipeline_config.cache_clear()


def _make_source(session, adapter_name: str = "workday", **overrides) -> Source:
    src = Source(
        source_key=f"{adapter_name}_test",
        employer_name="Test Co",
        board_name="Test board",
        base_url="https://test.example.com",
        hostname="test.example.com",
        source_type="ats",
        ats_family=adapter_name,
        adapter_name=adapter_name,
        active=True,
        seed_type="manual_seed",
        fingerprint=f"testco|{adapter_name}",
        **overrides,
    )
    session.add(src)
    session.commit()
    session.refresh(src)
    return src


def _make_run(session, source: Source, started_at: datetime, status: str = "success") -> SourceRun:
    run = SourceRun(
        source_id=source.id,
        run_type="discovery",
        status=status,
        trigger="test",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=5),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _make_attempt(session, source: Source, run: SourceRun) -> ExtractionAttempt:
    attempt = ExtractionAttempt(
        source_run_id=run.id,
        source_id=source.id,
        stage="discover",
        method="api",
        endpoint_url=source.base_url,
        success=True,
        diagnostics_blob={},
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _record(external_id: str, title: str = "Engineer") -> DiscoveredJobRecord:
    return DiscoveredJobRecord(
        external_job_id=external_id,
        title_raw=title,
        location_raw="London, UK",
        posted_at_raw="2026-04-01",
        summary_raw="...",
        discovered_url=f"https://example.com/jobs/{external_id}",
        apply_url=f"https://example.com/apply/{external_id}",
        listing_payload={},
        completeness_score=0.8,
        extraction_confidence=0.9,
        provenance={},
    )


def _enable_auto_mark_dead(monkeypatch, threshold: float = 0.75) -> None:
    monkeypatch.setattr(
        persistence,
        "_load_pipeline_config",
        lambda: {"auto_mark_dead_enabled": True, "auto_mark_dead_threshold": threshold},
    )


def _seed_history(
    session,
    source: Source,
    *external_ids: str,
    when: datetime,
) -> list[RawJob]:
    """Seed past RawJobs as if a previous run found them."""
    prior_run = _make_run(session, source, started_at=when - timedelta(seconds=10))
    prior_run.records_seen = len(external_ids)
    prior_run.raw_jobs_created = len(external_ids)
    session.commit()
    attempt = _make_attempt(session, source, prior_run)
    out: list[RawJob] = []
    for ext_id in external_ids:
        rec = _record(ext_id)
        rj = persistence.upsert_raw_job(
            session=session,
            source=source,
            source_run=prior_run,
            extraction_attempt=attempt,
            record=rec,
        )
        # Backdate so a future run's run_started_at is strictly after.
        rj.last_seen_at = when
        rj.first_seen_at = when
        out.append(rj)
    session.commit()
    return out


# ─── happy path ─────────────────────────────────────────────────────


def test_sweep_marks_unseen_jobs(session, monkeypatch):
    """Seed 4 (A,B,C,D) → run sees 3 (A,B,C, exactly 75%) → D marked dead.

    The 75% threshold is exclusive on the "below" side — i.e. exactly
    75% passes the gate. Sized this way to also act as a regression
    test on the boundary.
    """
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    _seed_history(session, src, "A", "B", "C", "D", when=long_ago)

    # Run 2 sees A, B, C only. records_seen = 3 / last = 4 = 75% exactly.
    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)
    attempt2 = _make_attempt(session, src, run2)
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("A"))
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("B"))
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("C"))

    persistence.finalise_source_run(session, run2, records_seen=3, raw_jobs_created=0)

    d = session.execute(
        select(RawJob).where(RawJob.external_job_id == "D")
    ).scalar_one()
    a = session.execute(
        select(RawJob).where(RawJob.external_job_id == "A")
    ).scalar_one()

    assert d.is_deleted_at_source is True
    assert d.deleted_at_source_at is not None
    assert a.is_deleted_at_source is False

    # Diagnostics blob records the marked_dead count.
    session.refresh(run2)
    assert (run2.diagnostics_blob or {}).get("marked_dead") == 1


# ─── safety guards ──────────────────────────────────────────────────


def test_sweep_skips_when_below_threshold(session, monkeypatch):
    """Run 1 saw 100; run 2 sees 50 (below 75%) → nothing marked dead."""
    _enable_auto_mark_dead(monkeypatch, threshold=0.75)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    ids = [f"J{i}" for i in range(100)]
    _seed_history(session, src, *ids, when=long_ago)

    # Run 2: only 50 records — well below 75% of 100.
    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)
    attempt2 = _make_attempt(session, src, run2)
    for ext_id in ids[:50]:
        persistence.upsert_raw_job(session, src, run2, attempt2, _record(ext_id))

    persistence.finalise_source_run(session, run2, records_seen=50, raw_jobs_created=0)

    dead_count = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead_count) == 0

    session.refresh(run2)
    assert (run2.diagnostics_blob or {}).get("sweep_skipped") is True
    assert "records_seen=50" in (run2.diagnostics_blob or {}).get("sweep_skip_reason", "")


def test_sweep_skips_when_records_zero(session, monkeypatch):
    """Zero-record runs are always suspicious — skip regardless of threshold."""
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    _seed_history(session, src, "A", "B", when=long_ago)

    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)

    persistence.finalise_source_run(session, run2, records_seen=0, raw_jobs_created=0)

    dead = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead) == 0

    session.refresh(run2)
    assert (run2.diagnostics_blob or {}).get("sweep_skipped") is True
    assert (run2.diagnostics_blob or {}).get("sweep_skip_reason") == "records_seen=0"


def test_sweep_skips_for_aggregator(session, monkeypatch):
    """Aggregator adapters don't guarantee complete coverage per run — skip."""
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="adzuna")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    _seed_history(session, src, "A", "B", when=long_ago)

    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)
    attempt2 = _make_attempt(session, src, run2)
    # Only see A — but adzuna isn't opted in, so B should NOT be marked dead.
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("A"))
    persistence.finalise_source_run(session, run2, records_seen=1, raw_jobs_created=0)

    dead = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead) == 0

    session.refresh(run2)
    skip_reason = (run2.diagnostics_blob or {}).get("sweep_skip_reason", "")
    assert "adzuna" in skip_reason and "not opted-in" in skip_reason


def test_sweep_skips_for_failed_run(session, monkeypatch):
    """Run with status='error' → no sweep, even if records_seen > 0."""
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    _seed_history(session, src, "A", "B", when=long_ago)

    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)

    # Manually set status to 'error' to mimic a failed run that
    # nonetheless calls finalise (this can happen mid-stage). The
    # finalise function sets status='success' itself, so we override
    # AFTER calling it to test the gate. To realistically test, we
    # invoke the sweep helper directly with a failed-status run.
    run2.status = "error"
    session.commit()

    outcome = persistence._maybe_sweep_dead_jobs(session, run2, records_seen=2)
    assert outcome is not None
    assert outcome.get("sweep_skipped") is True
    assert outcome.get("sweep_skip_reason") == "status=error"

    dead = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead) == 0


def test_feature_flag_off_no_sweep(session, monkeypatch):
    """Feature flag default-off → sweep helper returns None, no DB writes."""
    # Don't call _enable_auto_mark_dead — leave feature off.
    monkeypatch.setattr(
        persistence,
        "_load_pipeline_config",
        lambda: {"auto_mark_dead_enabled": False},
    )
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    _seed_history(session, src, "A", "B", when=long_ago)

    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)
    attempt2 = _make_attempt(session, src, run2)
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("A"))

    persistence.finalise_source_run(session, run2, records_seen=1, raw_jobs_created=0)

    dead = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead) == 0

    # When the flag is off the helper returns None — no diagnostic
    # entry should be written for the sweep.
    session.refresh(run2)
    assert "marked_dead" not in (run2.diagnostics_blob or {})
    assert "sweep_skipped" not in (run2.diagnostics_blob or {})


# ─── first-run edge case ────────────────────────────────────────────


def test_first_run_no_prior_history_runs_sweep(session, monkeypatch):
    """First-ever run has no prior history → threshold check returns None
    → sweep proceeds (since records_seen > 0). Nothing existed to mark
    dead, so marked_dead=0."""
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="workday")

    now = datetime.utcnow()
    run1 = _make_run(session, src, started_at=now)
    attempt1 = _make_attempt(session, src, run1)
    persistence.upsert_raw_job(session, src, run1, attempt1, _record("A"))

    persistence.finalise_source_run(session, run1, records_seen=1, raw_jobs_created=1)

    session.refresh(run1)
    # First run: no prior, sweep was attempted, found nothing to mark.
    assert (run1.diagnostics_blob or {}).get("marked_dead") == 0
    assert (run1.diagnostics_blob or {}).get("sweep_last_count") is None


# ─── rediscovery path ───────────────────────────────────────────────


def test_rediscovery_resets_dead_flag(session, monkeypatch):
    """A job marked dead, then re-seen on a later scrape, comes back to life.

    Sized so all threshold gates pass: seed 4 → run 2 sees 3 (75%
    exact) → D marked dead → run 3 sees 4 (133% of last_count=3) →
    D's flag cleared on upsert.
    """
    _enable_auto_mark_dead(monkeypatch)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=4)
    _seed_history(session, src, "A", "B", "C", "D", when=long_ago)

    # Run 2: only A, B, C → D marked dead.
    now1 = datetime.utcnow() - timedelta(hours=2)
    run2 = _make_run(session, src, started_at=now1)
    attempt2 = _make_attempt(session, src, run2)
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("A"))
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("B"))
    persistence.upsert_raw_job(session, src, run2, attempt2, _record("C"))
    persistence.finalise_source_run(session, run2, records_seen=3, raw_jobs_created=0)

    d_before = session.execute(
        select(RawJob).where(RawJob.external_job_id == "D")
    ).scalar_one()
    assert d_before.is_deleted_at_source is True

    # Run 3: D is back. The upsert path resets the flag.
    now2 = datetime.utcnow()
    run3 = _make_run(session, src, started_at=now2)
    attempt3 = _make_attempt(session, src, run3)
    persistence.upsert_raw_job(session, src, run3, attempt3, _record("A"))
    persistence.upsert_raw_job(session, src, run3, attempt3, _record("B"))
    persistence.upsert_raw_job(session, src, run3, attempt3, _record("C"))
    persistence.upsert_raw_job(session, src, run3, attempt3, _record("D"))
    persistence.finalise_source_run(session, run3, records_seen=4, raw_jobs_created=0)

    d_after = session.execute(
        select(RawJob).where(RawJob.external_job_id == "D")
    ).scalar_one()
    assert d_after.is_deleted_at_source is False
    assert d_after.deleted_at_source_at is None


# ─── threshold edge cases ───────────────────────────────────────────


def test_sweep_runs_at_exact_threshold(session, monkeypatch):
    """records_seen == 0.75 * last_count is allowed (>=, not >)."""
    _enable_auto_mark_dead(monkeypatch, threshold=0.75)
    src = _make_source(session, adapter_name="workday")

    long_ago = datetime.utcnow() - timedelta(hours=2)
    ids = [f"J{i}" for i in range(100)]
    _seed_history(session, src, *ids, when=long_ago)

    # Run 2: exactly 75 records → should run sweep.
    now = datetime.utcnow()
    run2 = _make_run(session, src, started_at=now)
    attempt2 = _make_attempt(session, src, run2)
    for ext_id in ids[:75]:
        persistence.upsert_raw_job(session, src, run2, attempt2, _record(ext_id))
    persistence.finalise_source_run(session, run2, records_seen=75, raw_jobs_created=0)

    # 25 jobs (J75..J99) should be marked dead.
    dead = session.execute(
        select(RawJob).where(RawJob.is_deleted_at_source.is_(True))
    ).scalars().all()
    assert len(dead) == 25
