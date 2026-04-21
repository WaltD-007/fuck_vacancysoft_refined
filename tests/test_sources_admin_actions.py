"""Tests for the Sources page admin-action endpoints.

Covers the two new endpoints introduced alongside the Sources card
"Agy job / Dead job / Wrong location" buttons:

  * DELETE /api/leads/{enriched_job_id}               (Dead job)
  * POST   /api/leads/{enriched_job_id}/flag-location (Wrong location)

The "Agy job" button reuses the existing POST /api/agency endpoint,
already covered elsewhere, so there's nothing new to test for it here.

Both endpoints are called directly (no TestClient) with SessionLocal
monkey-patched onto a fresh in-memory SQLite, matching the style in
test_paste_lead.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vacancysoft.api.routes import leads as leads_module
from vacancysoft.db.models import (
    Base,
    ClassificationResult,
    EnrichedJob,
    ExtractionAttempt,
    IntelligenceDossier,
    LocationReviewFlag,
    RawJob,
    ReviewQueueItem,
    ScoreResult,
    Source,
    SourceRun,
    User,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def patched_route(monkeypatch, session_factory):
    """Point the route module at the in-memory DB + stub out cache clears."""
    monkeypatch.setattr(leads_module, "SessionLocal", session_factory)
    monkeypatch.setattr(leads_module, "clear_dashboard_cache", lambda: None)
    # clear_ledger_caches is imported inside the handler; patch in-place.
    import vacancysoft.api.ledger as ledger_module
    monkeypatch.setattr(ledger_module, "clear_ledger_caches", lambda: None)
    return session_factory


def _seed_lead(session_factory, *, url: str = "https://example.com/job/1") -> tuple[str, str]:
    """Seed a minimal Source → RawJob → EnrichedJob chain plus a
    ClassificationResult / ScoreResult / ReviewQueueItem / IntelligenceDossier
    so the cascade can be verified. Returns (enriched_job_id, raw_job_id)."""
    with session_factory() as s:
        src = Source(
            source_key=f"seed-{uuid4()}",
            employer_name="Example Bank",
            base_url="https://example.com",
            hostname="example.com",
            source_type="direct",
            adapter_name="greenhouse",
            active=True,
            seed_type="manual_seed",
            fingerprint=f"fp-{uuid4()}",
            canonical_company_key="example-bank",
            capability_blob={},
        )
        s.add(src)
        s.flush()

        run = SourceRun(
            id=str(uuid4()),
            source_id=src.id,
            run_type="discovery",
            status="success",
            trigger="manual",
        )
        s.add(run)
        s.flush()
        attempt = ExtractionAttempt(
            id=str(uuid4()),
            source_run_id=run.id,
            source_id=src.id,
            stage="listing",
            method="api",
            success=True,
        )
        s.add(attempt)
        s.flush()
        raw = RawJob(
            source_id=src.id,
            source_run_id=run.id,
            extraction_attempt_id=attempt.id,
            external_job_id=f"ext-{uuid4()}",
            discovered_url=url,
            title_raw="Credit Risk Analyst",
            description_raw="Body",
            job_fingerprint=f"rfp-{uuid4()}",
            is_deleted_at_source=False,
        )
        s.add(raw)
        s.flush()

        ej = EnrichedJob(
            raw_job_id=raw.id,
            canonical_job_key=f"ck-{uuid4()}",
            title="Credit Risk Analyst",
            description_text="Body",
            location_city="London",
            location_country="UK",
            team="Example Bank",
            detail_fetch_status="enriched",
        )
        s.add(ej)
        s.flush()

        s.add(ClassificationResult(
            enriched_job_id=ej.id,
            classifier_version="test",
            taxonomy_version="test",
            primary_taxonomy_key="risk",
            classification_confidence=0.9,
            sub_specialism="Credit Risk",
            employment_type="Permanent",
            decision="accept",
        ))
        s.add(ScoreResult(
            enriched_job_id=ej.id,
            scoring_version="test",
            export_eligibility_score=0.8,
            export_decision="export",
        ))
        s.add(ReviewQueueItem(
            enriched_job_id=ej.id,
            queue_type="campaign",
            priority=50,
            reason_code="test",
            reason_summary="test",
            evidence_blob={"url": url},
            status="pending",
        ))
        s.add(IntelligenceDossier(
            enriched_job_id=ej.id,
            prompt_version="test",
            category_used="risk",
            model_used="gpt-test",
            core_problem="test",
            raw_response="{}",
        ))
        s.commit()
        return ej.id, raw.id


# ── DELETE /api/leads/{id} ───────────────────────────────────────────────


def test_delete_lead_cascades_and_flags_raw_job(patched_route, session_factory):
    ej_id, raw_id = _seed_lead(session_factory)

    result = leads_module.delete_lead(ej_id)

    assert result["message"] == "deleted"
    assert result["enriched_job_id"] == ej_id
    assert result["deleted_dossiers"] == 1
    assert result["deleted_queue_items"] == 1
    assert result["deleted_scores"] == 1
    assert result["deleted_classifications"] == 1

    with session_factory() as s:
        # Cascaded rows are gone
        assert s.execute(select(EnrichedJob).where(EnrichedJob.id == ej_id)).scalar_one_or_none() is None
        assert s.execute(select(ClassificationResult).where(ClassificationResult.enriched_job_id == ej_id)).scalar_one_or_none() is None
        assert s.execute(select(ScoreResult).where(ScoreResult.enriched_job_id == ej_id)).scalar_one_or_none() is None
        assert s.execute(select(ReviewQueueItem).where(ReviewQueueItem.enriched_job_id == ej_id)).scalar_one_or_none() is None
        assert s.execute(select(IntelligenceDossier).where(IntelligenceDossier.enriched_job_id == ej_id)).scalar_one_or_none() is None

        # RawJob is preserved but flagged
        raw = s.execute(select(RawJob).where(RawJob.id == raw_id)).scalar_one()
        assert raw.is_deleted_at_source is True


def test_delete_lead_missing_returns_404(patched_route):
    with pytest.raises(HTTPException) as exc:
        leads_module.delete_lead("does-not-exist")
    assert exc.value.status_code == 404


def test_delete_lead_then_enrichment_skips_raw_job(patched_route, session_factory):
    """End-to-end: after a Dead job delete, the enrichment pipeline's
    candidate query (pipelines/enrichment_persistence.py) should not
    re-surface the RawJob for re-enrichment.
    """
    from sqlalchemy import exists
    ej_id, raw_id = _seed_lead(session_factory)

    leads_module.delete_lead(ej_id)

    # Replicate the exact NOT EXISTS candidate filter used by the pipeline.
    with session_factory() as s:
        candidates = s.execute(
            select(RawJob).where(
                ~exists().where(EnrichedJob.raw_job_id == RawJob.id),
                RawJob.is_deleted_at_source.is_(False),
            )
        ).scalars().all()
        assert raw_id not in [c.id for c in candidates]


# ── POST /api/leads/{id}/flag-location ──────────────────────────────────


def test_flag_location_unparseable_note_queues_for_review(patched_route, session_factory):
    """A free-text note that doesn't parse as a location → queue only."""
    ej_id, _ = _seed_lead(session_factory)

    # Note contains no recognisable place name — normaliser returns
    # confidence 0.1, well below the 0.7 apply threshold.
    result = leads_module.flag_location(ej_id, {"note": "please check this one"})

    assert result["status"] == "queued"
    assert result["enriched_job_id"] == ej_id
    assert result["flag_id"]
    # No auto-apply fields
    assert "city" not in result

    with session_factory() as s:
        flag = s.execute(
            select(LocationReviewFlag).where(LocationReviewFlag.enriched_job_id == ej_id)
        ).scalar_one()
        assert flag.note == "please check this one"
        assert flag.resolved is False
        assert flag.resolved_at is None
        assert flag.flagged_by_user_id is None
        # Enriched job unchanged
        ej = s.execute(select(EnrichedJob).where(EnrichedJob.id == ej_id)).scalar_one()
        assert ej.location_city == "London"
        assert ej.location_country == "UK"


def test_flag_location_parseable_note_applies_correction(patched_route, session_factory):
    """Operator types 'Buffalo, NY, USA' → endpoint updates the enriched
    job directly, marks the flag row resolved, and reports status=applied.
    """
    ej_id, _ = _seed_lead(session_factory)

    result = leads_module.flag_location(ej_id, {"note": "Buffalo, NY, USA"})

    assert result["status"] == "applied"
    assert result["city"] == "Buffalo"
    assert result["country"] == "USA"
    assert ej_id in result["affected_enriched_ids"]

    with session_factory() as s:
        ej = s.execute(select(EnrichedJob).where(EnrichedJob.id == ej_id)).scalar_one()
        assert ej.location_city == "Buffalo"
        assert ej.location_country == "USA"

        flag = s.execute(
            select(LocationReviewFlag).where(LocationReviewFlag.enriched_job_id == ej_id)
        ).scalar_one()
        assert flag.resolved is True
        assert flag.resolved_at is not None
        assert flag.note == "Buffalo, NY, USA"


def test_flag_location_applies_to_all_url_siblings(patched_route, session_factory):
    """When two enriched_jobs share a URL (Google Jobs duplicate pattern),
    the correction fixes them all in one shot."""
    ej_id, raw_id = _seed_lead(session_factory, url="https://example.com/dup")

    # Seed a second enriched_job pointing at a different raw_job that
    # happens to share the same URL.
    with session_factory() as s:
        src = s.execute(select(Source)).scalars().first()
        run = SourceRun(
            id=str(uuid4()), source_id=src.id, run_type="discovery",
            status="success", trigger="manual",
        )
        s.add(run)
        s.flush()
        attempt = ExtractionAttempt(
            id=str(uuid4()), source_run_id=run.id, source_id=src.id,
            stage="listing", method="api", success=True,
        )
        s.add(attempt)
        s.flush()
        raw2 = RawJob(
            source_id=src.id,
            source_run_id=run.id,
            extraction_attempt_id=attempt.id,
            external_job_id=f"ext-dup-{uuid4()}",
            discovered_url="https://example.com/dup",  # same URL!
            title_raw="Credit Risk Analyst",
            job_fingerprint=f"rfp-dup-{uuid4()}",
            is_deleted_at_source=False,
        )
        s.add(raw2)
        s.flush()
        ej2 = EnrichedJob(
            raw_job_id=raw2.id,
            canonical_job_key=f"ck-{uuid4()}",
            title="Credit Risk Analyst",
            location_city="London",
            location_country="UK",
            team="Example Bank",
            detail_fetch_status="enriched",
        )
        s.add(ej2)
        s.commit()
        ej2_id = ej2.id

    result = leads_module.flag_location(ej_id, {"note": "Buffalo, NY, USA"})

    assert result["status"] == "applied"
    assert set(result["affected_enriched_ids"]) == {ej_id, ej2_id}

    with session_factory() as s:
        for eid in (ej_id, ej2_id):
            ej = s.execute(select(EnrichedJob).where(EnrichedJob.id == eid)).scalar_one()
            assert ej.location_city == "Buffalo"
            assert ej.location_country == "USA"


def test_flag_location_missing_lead_returns_404(patched_route):
    with pytest.raises(HTTPException) as exc:
        leads_module.flag_location("no-such-id", {"note": ""})
    assert exc.value.status_code == 404


def test_flag_location_empty_body_queues(patched_route, session_factory):
    """Operator may hit the button with no context — queued for review."""
    ej_id, _ = _seed_lead(session_factory)

    result = leads_module.flag_location(ej_id, None)

    assert result["status"] == "queued"
    with session_factory() as s:
        flag = s.execute(
            select(LocationReviewFlag).where(LocationReviewFlag.enriched_job_id == ej_id)
        ).scalar_one()
        assert flag.note == ""
        assert flag.resolved is False


def test_flag_location_accepts_user_id(patched_route, session_factory):
    ej_id, _ = _seed_lead(session_factory)

    # Seed a user so the FK resolves.
    with session_factory() as s:
        user = User(
            email="ab@example.com",
            display_name="AB",
        )
        s.add(user)
        s.commit()
        user_id = user.id

    leads_module.flag_location(ej_id, {"flagged_by_user_id": user_id, "note": "x"})

    with session_factory() as s:
        flag = s.execute(
            select(LocationReviewFlag).where(LocationReviewFlag.enriched_job_id == ej_id)
        ).scalar_one()
        assert flag.flagged_by_user_id == user_id


def test_flag_location_allows_multiple(patched_route, session_factory):
    """Each operator flag-event inserts a fresh row — we don't collapse."""
    ej_id, _ = _seed_lead(session_factory)

    leads_module.flag_location(ej_id, {"note": "first"})
    leads_module.flag_location(ej_id, {"note": "second"})

    with session_factory() as s:
        flags = list(s.execute(
            select(LocationReviewFlag).where(LocationReviewFlag.enriched_job_id == ej_id)
        ).scalars())
        assert len(flags) == 2
        assert {f.note for f in flags} == {"first", "second"}
