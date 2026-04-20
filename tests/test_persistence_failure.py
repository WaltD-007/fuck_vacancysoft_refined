"""Tests for persist_discovery_failure — the helper that turns an adapter
exception into a SourceRun + ExtractionAttempt row so the failure is queryable.

Added 2026-04-20 after a Lever pipeline run where the first 4 of 113 sources
failed without persisting any diagnostic trace.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import Base, ExtractionAttempt, Source, SourceRun
from vacancysoft.pipelines.persistence import persist_discovery_failure


@pytest.fixture()
def session():
    """Fresh in-memory SQLite with every table from Base created."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


@pytest.fixture()
def lever_source(session) -> Source:
    src = Source(
        source_key="lever_test_vaneck",
        employer_name="VanEck",
        board_name="VanEck careers",
        base_url="https://www.vaneck.com/us/en/careers/",
        hostname="www.vaneck.com",
        source_type="ats",
        ats_family="lever",
        adapter_name="lever",
        active=True,
        seed_type="manual_seed",
        fingerprint="vaneck|lever",
    )
    session.add(src)
    session.commit()
    session.refresh(src)
    return src


class TestPersistDiscoveryFailure:
    def test_writes_source_run_with_error_status(self, session, lever_source) -> None:
        exc = ValueError("Lever source_config requires slug — none provided and job_board_url (https://www.vaneck.com/us/en/careers/) is not a jobs.lever.co URL")
        run = persist_discovery_failure(session=session, source=lever_source, exc=exc, trigger="pipeline_discover")

        assert run.id is not None
        assert run.status == "error"
        assert run.source_id == lever_source.id
        assert run.errors_count == 1
        assert run.finished_at is not None

    def test_writes_diagnostics_blob_with_error_details(self, session, lever_source) -> None:
        exc = ValueError("some problem")
        run = persist_discovery_failure(session=session, source=lever_source, exc=exc)

        assert run.diagnostics_blob is not None
        assert run.diagnostics_blob["error_type"] == "ValueError"
        assert run.diagnostics_blob["error_message"] == "some problem"
        assert "ValueError: some problem" in run.diagnostics_blob["error"]

    def test_writes_extraction_attempt_row(self, session, lever_source) -> None:
        exc = RuntimeError("boom")
        run = persist_discovery_failure(session=session, source=lever_source, exc=exc)

        # Re-fetch from DB to confirm it's persisted, not just in session
        attempt = session.execute(
            select(ExtractionAttempt).where(ExtractionAttempt.source_run_id == run.id)
        ).scalar_one()
        assert attempt.success is False
        assert attempt.error_type == "RuntimeError"
        assert attempt.error_message == "boom"
        assert attempt.stage == "discover"

    def test_long_exception_message_is_truncated(self, session, lever_source) -> None:
        long = "x" * 3000
        run = persist_discovery_failure(session=session, source=lever_source, exc=Exception(long))

        # Should cap at 2000 chars to keep the JSON manageable
        assert len(run.diagnostics_blob["error_message"]) == 2000

    def test_queryable_by_status(self, session, lever_source) -> None:
        """Verify the canonical operator query pattern works after persistence."""
        persist_discovery_failure(session=session, source=lever_source, exc=ValueError("x"))

        errors = list(session.execute(
            select(SourceRun).where(SourceRun.status == "error")
        ).scalars())
        assert len(errors) == 1
        assert errors[0].source_id == lever_source.id
