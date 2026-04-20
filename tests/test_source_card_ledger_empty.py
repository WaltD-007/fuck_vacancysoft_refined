"""Tests for the "genuine empty" injection pass in _build_source_card_ledger.

A card should appear in the ledger with zero leads and zero raw_jobs only when
both the direct adapter and the aggregators have confirmed, within the last
24h, that they looked at the employer and found nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import (
    Base,
    ClassificationResult,
    EnrichedJob,
    ExtractionAttempt,
    RawJob,
    Source,
    SourceRun,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    """Fresh in-memory SQLite with every table from Base created."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    session,
    *,
    employer: str,
    adapter: str,
    active: bool = True,
) -> Source:
    src = Source(
        source_key=f"{adapter}:{employer.lower()}",
        employer_name=employer,
        base_url=f"https://{adapter}.example/{employer.lower()}",
        hostname=f"{adapter}.example",
        source_type="direct",
        adapter_name=adapter,
        active=active,
        seed_type="manual_seed",
        fingerprint=f"{adapter}:{employer.lower()}",
    )
    session.add(src)
    session.flush()
    return src


def _make_run(
    session,
    *,
    source_id: int,
    status: str,
    when: datetime,
    raw_jobs_created: int = 0,
) -> SourceRun:
    run = SourceRun(
        id=str(uuid4()),
        source_id=source_id,
        run_type="discovery",
        started_at=when,
        status=status,
        trigger="scheduled",
        raw_jobs_created=raw_jobs_created,
        created_at=when,
    )
    session.add(run)
    session.flush()
    return run


def _make_aggregator_raw_job(
    session,
    *,
    source_id: int,
    source_run_id: str,
    payload: dict,
    first_seen_at: datetime,
) -> RawJob:
    attempt = ExtractionAttempt(
        id=str(uuid4()),
        source_run_id=source_run_id,
        source_id=source_id,
        stage="discovery",
        method="api",
        success=True,
        created_at=first_seen_at,
    )
    session.add(attempt)
    session.flush()
    rj = RawJob(
        id=str(uuid4()),
        source_id=source_id,
        source_run_id=source_run_id,
        extraction_attempt_id=attempt.id,
        listing_payload=payload,
        job_fingerprint=str(uuid4()),
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        discovery_ts=first_seen_at,
        created_at=first_seen_at,
    )
    session.add(rj)
    session.flush()
    return rj


def _make_core_market_lead(
    session,
    *,
    direct_source: Source,
    direct_run_id: str,
    title: str = "Senior Risk Manager",
    country: str = "United Kingdom",
) -> None:
    """Seed a full RawJob→EnrichedJob→ClassificationResult chain for a direct
    source, primary_taxonomy_key="risk", so the ledger's step 1 picks it up."""
    attempt = ExtractionAttempt(
        id=str(uuid4()),
        source_run_id=direct_run_id,
        source_id=direct_source.id,
        stage="discovery",
        method="api",
        success=True,
    )
    session.add(attempt)
    session.flush()
    rj = RawJob(
        id=str(uuid4()),
        source_id=direct_source.id,
        source_run_id=direct_run_id,
        extraction_attempt_id=attempt.id,
        listing_payload={"title": title},
        job_fingerprint=str(uuid4()),
    )
    session.add(rj)
    session.flush()
    ej = EnrichedJob(
        id=str(uuid4()),
        raw_job_id=rj.id,
        canonical_job_key=str(uuid4()),
        title=title,
        location_country=country,
        detail_fetch_status="success",
    )
    session.add(ej)
    session.flush()
    cr = ClassificationResult(
        id=str(uuid4()),
        enriched_job_id=ej.id,
        classifier_version="test",
        taxonomy_version="test",
        primary_taxonomy_key="risk",
        decision="accepted",
    )
    session.add(cr)
    session.flush()


def _seed_qualifying(session, *, now: datetime, agg_payload: dict | None = None):
    """Case-A seed: direct Source 'Target Co' with fresh success + 0 raw jobs,
    and one aggregator Source with a fresh success run + one raw job mentioning
    some other employer (unless agg_payload is overridden)."""
    direct = _make_source(session, employer="Target Co", adapter="greenhouse")
    _make_run(session, source_id=direct.id, status="success", when=now - timedelta(hours=1))

    agg = _make_source(session, employer="Aggregator Pool", adapter="adzuna")
    agg_run = _make_run(session, source_id=agg.id, status="success", when=now - timedelta(hours=1))
    _make_aggregator_raw_job(
        session,
        source_id=agg.id,
        source_run_id=agg_run.id,
        payload=agg_payload or {"company": {"display_name": "Someone Else"}},
        first_seen_at=now - timedelta(hours=1),
    )
    session.commit()
    return direct, agg, agg_run


def _find_card(ledger, employer: str) -> dict | None:
    target = employer.lower().strip()
    for card in ledger:
        if card["employer_norm"] == target:
            return card
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_case_a_qualifies(session) -> None:
    """Direct success + 0 raw jobs + aggregator success + no mention → card appears."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    _seed_qualifying(session, now=now)

    ledger = _build_source_card_ledger(session)
    card = _find_card(ledger, "Target Co")
    assert card is not None, "expected synthetic empty card for Target Co"
    assert card["lead_ids"] == []
    assert card["raw_jobs_count"] == 0
    assert card["adapter_name"] == "greenhouse"
    assert card["card_id"] > 0, "should reuse real Source.id, not the negative virtual slot"
    assert card["last_run_status"] == "success"
    assert card["categories"] == {}


def test_case_b_direct_stale(session) -> None:
    """Direct success outside the 24h window → card does not appear."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    direct = _make_source(session, employer="Target Co", adapter="greenhouse")
    _make_run(session, source_id=direct.id, status="success", when=now - timedelta(hours=25))
    agg = _make_source(session, employer="Aggregator Pool", adapter="adzuna")
    _make_run(session, source_id=agg.id, status="success", when=now - timedelta(hours=1))
    session.commit()

    ledger = _build_source_card_ledger(session)
    assert _find_card(ledger, "Target Co") is None


def test_case_c_aggregator_match(session) -> None:
    """Aggregator payload mentions the employer → card does not appear."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    _seed_qualifying(
        session,
        now=now,
        agg_payload={"company": {"display_name": "Target Co"}},
    )

    ledger = _build_source_card_ledger(session)
    assert _find_card(ledger, "Target Co") is None


def test_case_d_aggregator_gate(session) -> None:
    """No aggregator SourceRun in last 24h → card does not appear."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    direct = _make_source(session, employer="Target Co", adapter="greenhouse")
    _make_run(session, source_id=direct.id, status="success", when=now - timedelta(hours=1))
    # Aggregator exists but last run is stale
    agg = _make_source(session, employer="Aggregator Pool", adapter="adzuna")
    _make_run(session, source_id=agg.id, status="success", when=now - timedelta(hours=30))
    session.commit()

    ledger = _build_source_card_ledger(session)
    assert _find_card(ledger, "Target Co") is None


def test_case_e_with_leads_wins(session) -> None:
    """Employer already has a core-market lead → lead card survives, not overwritten."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    direct, _agg, _agg_run = _seed_qualifying(session, now=now)
    # Attach a core-market classification to the same employer's direct source.
    lead_run = _make_run(
        session, source_id=direct.id, status="success", when=now - timedelta(minutes=30)
    )
    _make_core_market_lead(session, direct_source=direct, direct_run_id=lead_run.id)
    session.commit()

    ledger = _build_source_card_ledger(session)
    card = _find_card(ledger, "Target Co")
    assert card is not None
    assert len(card["lead_ids"]) == 1, "With-Leads card should survive; not overwritten"
    assert card["categories"], "real card has categories populated"


def test_case_f_country_filter(session) -> None:
    """Country-filtered ledger must not inject synthetic empty cards."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    _seed_qualifying(session, now=now)

    ledger = _build_source_card_ledger(session, country="United Kingdom")
    assert _find_card(ledger, "Target Co") is None


def test_case_g_direct_has_raw_jobs_does_not_qualify(session) -> None:
    """If the direct source has any current raw_jobs, the card is not empty."""
    from vacancysoft.api.ledger import _build_source_card_ledger

    now = datetime.utcnow()
    direct, _agg, _agg_run = _seed_qualifying(session, now=now)
    # Give the direct source a raw job (not classified into core markets).
    direct_run = _make_run(
        session, source_id=direct.id, status="success", when=now - timedelta(minutes=15)
    )
    attempt = ExtractionAttempt(
        id=str(uuid4()),
        source_run_id=direct_run.id,
        source_id=direct.id,
        stage="discovery",
        method="api",
        success=True,
    )
    session.add(attempt)
    session.flush()
    session.add(
        RawJob(
            id=str(uuid4()),
            source_id=direct.id,
            source_run_id=direct_run.id,
            extraction_attempt_id=attempt.id,
            listing_payload={"title": "Marketing Manager"},
            job_fingerprint=str(uuid4()),
        )
    )
    session.commit()

    ledger = _build_source_card_ledger(session)
    assert _find_card(ledger, "Target Co") is None
