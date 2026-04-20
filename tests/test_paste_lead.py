"""Tests for the POST /api/leads/paste endpoint.

Exercises the three meaningful branches end-to-end without a real FastAPI
test client or Redis:

1. Happy path — paste a fresh URL → full pipeline runs, one row in each of
   RawJob / EnrichedJob / ClassificationResult / ScoreResult /
   ReviewQueueItem, endpoint returns status=queued.

2. Dedupe — a URL that already has an EnrichedJob → no new
   RawJob/EnrichedJob rows, exactly one new ReviewQueueItem,
   endpoint returns status=reused.

3. Scraper error — scrape_advert returns status=error → HTTPException(422),
   zero new DB rows.

The test patches:
  * `scrape_advert` (in api.routes.leads) with a canned dict
  * `SessionLocal` (in api.routes.leads) with a per-test in-memory SQLite
  * `clear_dashboard_cache` and `_enqueue_process_lead` with no-ops

so the route function can be invoked directly as `await paste_lead(req, req_mock)`.
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
    + canned scraper. Returns a SimpleNamespace the test configures.

    The test mutates `namespace.scrape_result` before calling the route
    so each case can return its own canned scraper payload.
    """
    state = SimpleNamespace(
        scrape_result=None,
        enqueue_calls=[],
        cache_cleared=False,
    )

    async def fake_scrape_advert(url, *, workday=None, timeout_s=120):
        return state.scrape_result

    async def fake_enqueue(request, item_id, url, *, company, title):
        state.enqueue_calls.append(
            {"item_id": item_id, "url": url, "company": company, "title": title}
        )

    def fake_clear_cache():
        state.cache_cleared = True

    # Patch SessionLocal (used inside the route with `with SessionLocal() as s:`)
    monkeypatch.setattr(leads_module, "SessionLocal", session_factory)
    # The route imports scrape_advert at call time (`from vacancysoft.intelligence.url_scrape import scrape_advert`)
    # so patch the source module.
    monkeypatch.setattr(
        "vacancysoft.intelligence.url_scrape.scrape_advert",
        fake_scrape_advert,
    )
    monkeypatch.setattr(leads_module, "_enqueue_process_lead", fake_enqueue)
    monkeypatch.setattr(leads_module, "clear_dashboard_cache", fake_clear_cache)

    return state


def _mock_request():
    """Minimal FastAPI Request stand-in — the route only reads app.state.redis."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))


# ── Test 1: Happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_creates_full_pipeline_rows(patched_route, session_factory):
    """Fresh URL → one row in each pipeline table, status=queued."""
    patched_route.scrape_result = {
        "status": "success",
        "url": "https://boards.greenhouse.io/examplebank/jobs/12345",
        "finalUrl": "https://boards.greenhouse.io/examplebank/jobs/12345",
        "title": "Senior Credit Risk Analyst",
        "company": "Example Bank",
        "location": "London, UK",
        "description": "We are hiring a senior credit risk analyst to cover our wholesale book. Must have 5+ years of counterparty credit risk experience.",
        "descriptionLength": 128,
        "wasTruncated": False,
        "postedDate": "2026-04-10",
        "selectorUsed": "jobposting-json-ld",
        "error": None,
    }

    result = await leads_module.paste_lead(
        PasteLeadRequest(url="https://boards.greenhouse.io/examplebank/jobs/12345"),
        _mock_request(),
    )

    assert result["status"] == "queued"
    assert result["item_id"]
    assert result["enriched_id"]

    with session_factory() as s:
        assert s.execute(select(Source).where(Source.source_key == "manual_paste")).scalar_one()
        raw_jobs = list(s.execute(select(RawJob)).scalars())
        assert len(raw_jobs) == 1
        assert raw_jobs[0].discovered_url == "https://boards.greenhouse.io/examplebank/jobs/12345"
        assert raw_jobs[0].title_raw == "Senior Credit Risk Analyst"

        enriched_rows = list(s.execute(select(EnrichedJob)).scalars())
        assert len(enriched_rows) == 1
        assert enriched_rows[0].title == "Senior Credit Risk Analyst"
        assert enriched_rows[0].location_country == "UK"

        assert len(list(s.execute(select(ClassificationResult)).scalars())) == 1
        assert len(list(s.execute(select(ScoreResult)).scalars())) == 1

        items = list(s.execute(select(ReviewQueueItem)).scalars())
        assert len(items) == 1
        assert items[0].status == "pending"
        assert items[0].enriched_job_id == result["enriched_id"]

    assert patched_route.cache_cleared
    assert len(patched_route.enqueue_calls) == 1
    assert patched_route.enqueue_calls[0]["title"] == "Senior Credit Risk Analyst"


# ── Test 2: Dedupe ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_reuses_existing_enriched(patched_route, session_factory):
    """An existing EnrichedJob under the pasted URL → reused, no new persistence rows."""
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

    # No scraper call should happen on dedupe path — but set a sentinel anyway.
    patched_route.scrape_result = {"status": "error", "error": "should not be called"}

    result = await leads_module.paste_lead(PasteLeadRequest(url=url), _mock_request())

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


# ── Test 3: Scraper error ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paste_scraper_error_raises_422(patched_route, session_factory):
    """Scraper returns status=error → HTTPException(422), no rows created."""
    patched_route.scrape_result = {
        "status": "error",
        "url": "https://example.com/not-a-job",
        "error": "Playwright runner unreachable",
        "title": "",
        "company": "",
        "location": "",
        "description": "",
        "postedDate": "",
    }

    with pytest.raises(HTTPException) as excinfo:
        await leads_module.paste_lead(
            PasteLeadRequest(url="https://example.com/not-a-job"),
            _mock_request(),
        )

    assert excinfo.value.status_code == 422
    assert "Playwright runner unreachable" in excinfo.value.detail

    with session_factory() as s:
        assert len(list(s.execute(select(RawJob)).scalars())) == 0
        assert len(list(s.execute(select(EnrichedJob)).scalars())) == 0
        assert len(list(s.execute(select(ReviewQueueItem)).scalars())) == 0

    assert len(patched_route.enqueue_calls) == 0


# ── Test 4: Company parsed from "Role at Company" title ──────────────────


@pytest.mark.asyncio
async def test_paste_extracts_company_from_title(patched_route, session_factory):
    """When the runner doesn't return a structured company but the scraped
    title has the 'X at Y' shape (common on ATS careers pages like Barclays),
    the parsed company lands on enriched.team — NOT the '(Manual paste)'
    Source.employer_name placeholder.
    """
    patched_route.scrape_result = {
        "status": "success",
        "url": "https://search.jobs.barclays/job/glasgow/credit-risk-officer/13015/123",
        "finalUrl": "https://search.jobs.barclays/job/glasgow/credit-risk-officer/13015/123",
        # Runner's extractor didn't populate structured metadata — no JSON-LD
        # on this page. Only the document.title came through.
        "title": "Credit Risk Officer at Barclays",
        "company": "",
        "location": "",
        "description": (
            "Credit Risk Officer. Glasgow, United Kingdom. "
            "Business Area: Risk. Area of Expertise: Risk and Quantitative Analytics. "
            "Permanent. In Risk Barclays develops, recommends, and implements controls. "
            "You will analyse counterparty credit risk and manage portfolio limits."
        ),
        "descriptionLength": 280,
        "wasTruncated": False,
        "postedDate": "",
        "selectorUsed": "main",
        "error": None,
    }

    result = await leads_module.paste_lead(
        PasteLeadRequest(url="https://search.jobs.barclays/job/glasgow/credit-risk-officer/13015/123"),
        _mock_request(),
    )
    assert result["status"] == "queued"

    with session_factory() as s:
        enriched = list(s.execute(select(EnrichedJob)).scalars())[0]
        # Title is the cleaned role; the "at Barclays" suffix is stripped
        assert enriched.title == "Credit Risk Officer"
        # enriched.team is the parsed employer — NOT the Source placeholder
        assert enriched.team == "Barclays"

    # Worker gets the real company in the enqueue args so its fuzzy
    # EnrichedJob lookup actually has something to match on.
    assert patched_route.enqueue_calls[0]["company"] == "Barclays"
    assert patched_route.enqueue_calls[0]["title"] == "Credit Risk Officer"


# ── Test 5: _resolve_company guards against placeholder Source names ─────


def test_resolve_company_ignores_manual_paste_placeholder():
    """The dossier prompt + HM SerpApi path must never see
    '(Manual paste)' as the company — that breaks Google queries and causes
    gpt-4o-mini to hallucinate hiring managers. The resolver prefers
    enriched.team, falls through to listing_payload, and skips placeholder
    strings in Source.employer_name.
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


# ── Test 6: LinkedIn URLs auto-rejected before scraping ──────────────────


@pytest.mark.asyncio
async def test_paste_rejects_linkedin_urls(patched_route, session_factory):
    """LinkedIn job URLs are auto-rejected with 422. No scrape call, no DB
    rows. The error message points the operator to the ATS alternative.
    """
    linkedin_variants = [
        "https://www.linkedin.com/jobs/view/head-of-credit-risk-at-abound-4383256061/",
        "https://linkedin.com/jobs/view/4387495472/",
        "https://uk.linkedin.com/jobs/view/123456",
        "https://www.linkedin.com/comm/jobs/view/987654",
    ]
    # Sentinel — scraper MUST NOT be invoked; if it is, this payload makes
    # the pipeline run and the DB-count assertion below catches it.
    patched_route.scrape_result = {
        "status": "success",
        "title": "Should not reach runner",
        "company": "x",
        "location": "London, UK",
        "description": "x" * 500,
        "postedDate": "",
        "error": None,
    }
    for url in linkedin_variants:
        with pytest.raises(HTTPException) as excinfo:
            await leads_module.paste_lead(
                PasteLeadRequest(url=url),
                _mock_request(),
            )
        assert excinfo.value.status_code == 422, url
        assert "LinkedIn" in excinfo.value.detail
        assert "company website" in excinfo.value.detail.lower()

    # No DB rows created across all 4 rejections
    with session_factory() as s:
        assert len(list(s.execute(select(RawJob)).scalars())) == 0
        assert len(list(s.execute(select(EnrichedJob)).scalars())) == 0
        assert len(list(s.execute(select(ReviewQueueItem)).scalars())) == 0
    assert len(patched_route.enqueue_calls) == 0


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
