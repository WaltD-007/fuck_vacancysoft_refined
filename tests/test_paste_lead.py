"""Tests for the POST /api/leads/paste endpoint (text-paste flow).

Exercises the meaningful branches end-to-end without a real FastAPI test
client or Redis:

1. Happy path with URL — full pipeline runs, one row in each of
   RawJob / EnrichedJob / ClassificationResult / ScoreResult /
   ReviewQueueItem, endpoint returns status=queued.

2. Happy path without URL — same, but RawJob.discovered_url IS NULL and
   the fingerprint is SHA1 of the pasted text.

3. Dedupe — a URL that already has an EnrichedJob → no new
   RawJob/EnrichedJob rows, exactly one new ReviewQueueItem, endpoint
   returns status=reused.

4. LLM error — extract_advert_fields raises → HTTPException(422),
   zero new DB rows.

5. LinkedIn URL in the optional URL field → HTTPException(422).

6. Advert text too short → HTTPException(400).

7. Filter bypass — a US-based role that today's pipeline would 422 on
   (is_allowed_country rejects outside the core market) still creates
   the EnrichedJob when called from the paste route (skip_filters=True).

The tests patch:
  * ``extract_advert_fields`` (in api.routes.leads) with a canned dict
  * ``SessionLocal`` (in api.routes.leads) with a per-test in-memory SQLite
  * ``clear_dashboard_cache`` and ``_enqueue_process_lead`` with no-ops

so the route function can be invoked directly as
``await paste_lead(req, req_mock)``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vacancysoft.api.routes import leads as leads_module
from vacancysoft.api.schemas import PasteLeadRequest
from vacancysoft.db.models import (
    Base,
    ClassificationResult,
    EnrichedJob,
    ExtractionAttempt,
    RawJob,
    ReviewQueueItem,
    ScoreResult,
    Source,
    SourceRun,
)


# A realistic advert body used across tests. Long enough to clear the
# 80-char minimum and to exercise the SHA1-based fingerprint path.
_DEFAULT_ADVERT_TEXT = (
    "Senior Credit Risk Analyst — Example Bank, London, UK.\n\n"
    "We are hiring a senior credit risk analyst to cover our wholesale "
    "book. Must have 5+ years of counterparty credit risk experience. "
    "Responsibilities include rating counterparties, setting exposure "
    "limits, and presenting to the credit committee."
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory():
    """Fresh in-memory SQLite with every table created."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def patched_route(monkeypatch, session_factory):
    """Wire the route module to use the in-memory session + no-op enqueue
    + canned extractor. Returns a SimpleNamespace the test configures.

    The test mutates ``namespace.extract_result`` (or
    ``namespace.extract_raises``) before calling the route so each case
    can control the LLM's canned payload.
    """
    state = SimpleNamespace(
        extract_result=None,
        extract_raises=None,
        enqueue_calls=[],
        cache_cleared=False,
    )

    async def fake_extract_advert_fields(advert_text):
        if state.extract_raises is not None:
            raise state.extract_raises
        return state.extract_result

    async def fake_enqueue(request, item_id, url, *, company, title):
        state.enqueue_calls.append(
            {"item_id": item_id, "url": url, "company": company, "title": title}
        )

    def fake_clear_cache():
        state.cache_cleared = True

    # Patch SessionLocal (used inside the route with `with SessionLocal() as s:`)
    monkeypatch.setattr(leads_module, "SessionLocal", session_factory)
    # The route imports extract_advert_fields at call time
    # (`from vacancysoft.intelligence.advert_extraction import extract_advert_fields`)
    # so patch the source module.
    monkeypatch.setattr(
        "vacancysoft.intelligence.advert_extraction.extract_advert_fields",
        fake_extract_advert_fields,
    )
    monkeypatch.setattr(leads_module, "_enqueue_process_lead", fake_enqueue)
    monkeypatch.setattr(leads_module, "clear_dashboard_cache", fake_clear_cache)

    return state


def _mock_request():
    """Minimal FastAPI Request stand-in — the route only reads app.state.redis."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


def _success_meta(**overrides):
    """Canned extract_advert_fields() result mirroring scrape_advert()'s shape."""
    base = {
        "status": "success",
        "title": "Senior Credit Risk Analyst",
        "company": "Example Bank",
        "location": "London, UK",
        "description": _DEFAULT_ADVERT_TEXT,
        "postedDate": "2026-04-10",
        "model": "gpt-4o-mini",
        "tokens_total": 180,
        "latency_ms": 820,
    }
    base.update(overrides)
    return base


# ── Test 1: Happy path with URL ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_with_url_creates_full_pipeline_rows(
    patched_route, session_factory
):
    """Fresh text + URL → one row in each pipeline table, status=queued."""
    patched_route.extract_result = _success_meta()

    result = await leads_module.paste_lead(
        PasteLeadRequest(
            advert_text=_DEFAULT_ADVERT_TEXT,
            url="https://boards.greenhouse.io/examplebank/jobs/12345",
        ),
        _mock_request(),
    )

    assert result["status"] == "queued"
    assert result["item_id"]
    assert result["enriched_id"]

    with session_factory() as s:
        assert s.execute(
            select(Source).where(Source.source_key == "manual_paste")
        ).scalar_one()
        raw_jobs = list(s.execute(select(RawJob)).scalars())
        assert len(raw_jobs) == 1
        assert (
            raw_jobs[0].discovered_url
            == "https://boards.greenhouse.io/examplebank/jobs/12345"
        )
        assert raw_jobs[0].title_raw == "Senior Credit Risk Analyst"
        # Full pasted text preserved losslessly in description_raw
        assert raw_jobs[0].description_raw == _DEFAULT_ADVERT_TEXT
        # Provenance reflects the LLM-extract mode (not the old
        # "manual_paste" value).
        assert raw_jobs[0].provenance_blob["mode"] == "manual_paste_text"

        enriched_rows = list(s.execute(select(EnrichedJob)).scalars())
        assert len(enriched_rows) == 1
        assert enriched_rows[0].title == "Senior Credit Risk Analyst"
        assert enriched_rows[0].location_country == "UK"
        assert enriched_rows[0].team == "Example Bank"

        assert len(list(s.execute(select(ClassificationResult)).scalars())) == 1
        assert len(list(s.execute(select(ScoreResult)).scalars())) == 1

        items = list(s.execute(select(ReviewQueueItem)).scalars())
        assert len(items) == 1
        assert items[0].status == "pending"
        assert items[0].enriched_job_id == result["enriched_id"]

    assert patched_route.cache_cleared
    assert len(patched_route.enqueue_calls) == 1
    assert patched_route.enqueue_calls[0]["title"] == "Senior Credit Risk Analyst"


# ── Test 2: Happy path without URL ───────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_without_url_creates_lead(patched_route, session_factory):
    """No URL supplied → lead created with discovered_url IS NULL and a
    text-hash fingerprint (so multiple URL-less pastes still get unique
    external_job_ids).
    """
    patched_route.extract_result = _success_meta()

    result = await leads_module.paste_lead(
        PasteLeadRequest(advert_text=_DEFAULT_ADVERT_TEXT),
        _mock_request(),
    )

    assert result["status"] == "queued"

    with session_factory() as s:
        raw = list(s.execute(select(RawJob)).scalars())[0]
        assert raw.discovered_url is None
        assert raw.canonical_url is None
        assert raw.apply_url is None
        # Fingerprint came from the text, not a URL — it's a 40-char hex
        # SHA1 (MD5 would be 32 chars).
        assert len(raw.job_fingerprint) == 40


# ── Test 3: Dedupe on URL ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_with_url_reuses_existing_enriched(
    patched_route, session_factory
):
    """An existing EnrichedJob under the pasted URL → reused, no new
    persistence rows, no LLM call.
    """
    url = "https://boards.greenhouse.io/examplebank/jobs/99999"

    # Seed an existing lead (Source → SourceRun → ExtractionAttempt → RawJob → EnrichedJob).
    with session_factory() as s:
        src = Source(
            source_key="seed",
            employer_name="Example Bank",
            base_url="https://boards.greenhouse.io/examplebank",
            hostname="boards.greenhouse.io",
            source_type="direct",
            adapter_name="greenhouse",
            active=True,
            seed_type="manual_seed",
            fingerprint="seed",
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
            id=str(uuid4()),
            source_id=src.id,
            source_run_id=run.id,
            extraction_attempt_id=attempt.id,
            discovered_url=url,
            title_raw="Senior Credit Risk Analyst",
            location_raw="London",
            job_fingerprint="seed-rj",
        )
        s.add(raw)
        s.flush()
        enriched = EnrichedJob(
            raw_job_id=raw.id,
            canonical_job_key="seed-ej",
            title="Senior Credit Risk Analyst",
            location_text="London",
            location_country="UK",
            detail_fetch_status="enriched",
        )
        s.add(enriched)
        s.commit()
        pre_enriched_id = enriched.id

    # Sentinel — extractor MUST NOT be called on the dedupe path.
    patched_route.extract_raises = RuntimeError("should not be called")

    result = await leads_module.paste_lead(
        PasteLeadRequest(advert_text=_DEFAULT_ADVERT_TEXT, url=url),
        _mock_request(),
    )

    assert result["status"] == "reused"
    assert result["enriched_id"] == pre_enriched_id

    with session_factory() as s:
        # Exactly one RawJob and one EnrichedJob — no duplicates from the paste.
        assert len(list(s.execute(select(RawJob)).scalars())) == 1
        assert len(list(s.execute(select(EnrichedJob)).scalars())) == 1
        # Exactly one new queue item added by the paste path.
        items = list(s.execute(select(ReviewQueueItem)).scalars())
        assert len(items) == 1
        assert items[0].enriched_job_id == pre_enriched_id

    # Enqueue still fires so the worker refreshes the campaign.
    assert len(patched_route.enqueue_calls) == 1


# ── Test 4: LLM error ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_llm_error_raises_422(patched_route, session_factory):
    """extract_advert_fields raises → HTTPException(422), no rows created."""
    patched_route.extract_raises = RuntimeError("LLM returned non-JSON content")

    with pytest.raises(HTTPException) as excinfo:
        await leads_module.paste_lead(
            PasteLeadRequest(advert_text=_DEFAULT_ADVERT_TEXT),
            _mock_request(),
        )

    assert excinfo.value.status_code == 422
    assert "LLM returned non-JSON" in excinfo.value.detail

    with session_factory() as s:
        assert len(list(s.execute(select(RawJob)).scalars())) == 0
        assert len(list(s.execute(select(EnrichedJob)).scalars())) == 0
        assert len(list(s.execute(select(ReviewQueueItem)).scalars())) == 0

    assert len(patched_route.enqueue_calls) == 0


# ── Test 5: LinkedIn URL in the optional URL field ───────────────────────


@pytest.mark.asyncio
async def test_paste_rejects_linkedin_url(patched_route, session_factory):
    """A LinkedIn URL in the optional URL field → 422. No LLM call, no rows.

    Text-only pastes for LinkedIn roles are expected — the rejection is
    specifically about the URL field.
    """
    linkedin_variants = [
        "https://www.linkedin.com/jobs/view/head-of-credit-risk-at-abound-4383256061/",
        "https://linkedin.com/jobs/view/4387495472/",
        "https://uk.linkedin.com/jobs/view/123456",
        "https://www.linkedin.com/comm/jobs/view/987654",
    ]
    patched_route.extract_raises = RuntimeError("extractor must not be invoked")

    for url in linkedin_variants:
        with pytest.raises(HTTPException) as excinfo:
            await leads_module.paste_lead(
                PasteLeadRequest(advert_text=_DEFAULT_ADVERT_TEXT, url=url),
                _mock_request(),
            )
        assert excinfo.value.status_code == 422, url
        assert "LinkedIn" in excinfo.value.detail

    with session_factory() as s:
        assert len(list(s.execute(select(RawJob)).scalars())) == 0
        assert len(list(s.execute(select(EnrichedJob)).scalars())) == 0
        assert len(list(s.execute(select(ReviewQueueItem)).scalars())) == 0
    assert len(patched_route.enqueue_calls) == 0


# ── Test 6: Too-short advert_text ────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_advert_text_too_short(patched_route, session_factory):
    """advert_text shorter than 80 chars → HTTPException(400)."""
    patched_route.extract_raises = RuntimeError("extractor must not be invoked")

    with pytest.raises(HTTPException) as excinfo:
        await leads_module.paste_lead(
            PasteLeadRequest(advert_text="Senior Credit Risk Analyst — London"),
            _mock_request(),
        )
    assert excinfo.value.status_code == 400
    assert "too short" in excinfo.value.detail

    with session_factory() as s:
        assert len(list(s.execute(select(RawJob)).scalars())) == 0


# ── Test 7: Filter bypass — a US role that today's filters would reject ──


@pytest.mark.asyncio
async def test_paste_filter_bypass_allows_us_role(patched_route, session_factory):
    """A US-based role (outside the core-market allow-list) should still
    create an EnrichedJob when pasted — the paste route calls
    persist_enrichment_for_raw_job with skip_filters=True.
    """
    patched_route.extract_result = _success_meta(
        title="Senior Credit Risk Analyst",
        company="Example Bank",
        location="Buffalo, NY, USA",
    )

    result = await leads_module.paste_lead(
        PasteLeadRequest(advert_text=_DEFAULT_ADVERT_TEXT),
        _mock_request(),
    )

    assert result["status"] == "queued"

    with session_factory() as s:
        enriched_rows = list(s.execute(select(EnrichedJob)).scalars())
        assert len(enriched_rows) == 1
        assert enriched_rows[0].location_country == "USA"


# ── _resolve_company still guards against placeholder Source names ──────


def test_resolve_company_ignores_manual_paste_placeholder():
    """The dossier prompt + HM SerpApi path must never see
    '(Manual paste)' as the company — that breaks Google queries and
    causes gpt-4o-mini to hallucinate hiring managers. The resolver
    prefers enriched.team, falls through to listing_payload, and skips
    placeholder strings in Source.employer_name.
    """
    from vacancysoft.intelligence.dossier import _resolve_company

    source = SimpleNamespace(
        employer_name="(Manual paste)",
        adapter_name="manual_paste",
    )
    raw = SimpleNamespace(listing_payload={"company_name": "Barclays"})

    # Path 1: enriched.team is authoritative when set
    enriched_with_team = SimpleNamespace(team="Barclays")
    assert _resolve_company(enriched_with_team, raw, source) == "Barclays"

    # Path 2: enriched.team empty → listing_payload fallback
    enriched_empty = SimpleNamespace(team=None)
    assert _resolve_company(enriched_empty, raw, source) == "Barclays"

    # Path 3: enriched.team holds the placeholder (pathological case) →
    # resolver skips it rather than feeding "(Manual paste)" to the prompt
    enriched_placeholder = SimpleNamespace(team="(Manual paste)")
    assert _resolve_company(enriched_placeholder, raw, source) == "Barclays"

    # Path 4: nothing resolvable → empty string, not the placeholder
    enriched_empty = SimpleNamespace(team=None)
    raw_empty = SimpleNamespace(listing_payload={})
    assert _resolve_company(enriched_empty, raw_empty, source) == ""


# ── LinkedIn URL detector unit test (unchanged) ──────────────────────────


def test_is_linkedin_job_url_matches_expected_variants():
    """Unit test the URL detector — LinkedIn job patterns only, nothing else."""
    from vacancysoft.api.routes.leads import _is_linkedin_job_url

    should_match = [
        "https://www.linkedin.com/jobs/view/12345",
        "https://linkedin.com/jobs/view/12345",
        "https://uk.linkedin.com/jobs/view/12345",
        "https://www.linkedin.com/jobs/collections/...",
        "https://www.linkedin.com/comm/jobs/view/12345",
        "HTTPS://WWW.LINKEDIN.COM/jobs/view/12345",  # case-insensitive
    ]
    should_not_match = [
        # LinkedIn but not a job URL — company page, profile, etc.
        "https://www.linkedin.com/in/someone",
        "https://www.linkedin.com/company/barclays",
        # Different domain
        "https://search.jobs.barclays/job/glasgow/credit-risk-officer/13015/123",
        "https://boards.greenhouse.io/examplebank/jobs/12345",
        "https://jobs.lever.co/example/abc",
        # Lookalike domain shouldn't false-positive
        "https://fake-linkedin.com/jobs/view/12345",
        "",
        "not a url",
    ]
    for url in should_match:
        assert _is_linkedin_job_url(url), f"Expected match for {url!r}"
    for url in should_not_match:
        assert not _is_linkedin_job_url(url), f"Unexpected match for {url!r}"
