"""Source-card ledger construction and associated caches.

`_build_source_card_ledger` produces the per-employer card list used by
both `GET /api/sources` and `GET /api/sources/{id}/jobs`. It is the most
complex piece of the API and is now isolated here so it can be imported
without pulling in all the route handlers, and tested directly against
an in-memory SQLite engine (see `tests/test_source_card_ledger_empty.py`).

Extracted verbatim from `api/server.py` during the Week 4 split.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ScoreResult, Source


# ── Constants used across the ledger and every route group ────────────────

_CORE_MARKETS = ("risk", "quant", "compliance", "audit", "cyber", "legal", "front_office")

_CATEGORY_LABELS = {
    "risk": "Risk",
    "quant": "Quant",
    "compliance": "Compliance",
    "audit": "Audit",
    "cyber": "Cyber",
    "legal": "Legal",
    "front_office": "Front Office",
}

_AGGREGATOR_ADAPTERS = {"adzuna", "reed", "efinancialcareers", "google_jobs", "coresignal"}


# ── Per-country caches ────────────────────────────────────────────────────

_sources_cache: dict[str, tuple[float, list]] = {}      # cached SourceOut lists, by country
_ledger_cache: dict[str, tuple[float, list]] = {}       # cached _LedgerCard lists, by country
_SOURCES_CACHE_TTL = 30  # seconds


def clear_ledger_caches() -> None:
    """Drop both cached lists. Call this from any handler that mutates
    the source / raw_job / classification tables so the next `/api/sources`
    request rebuilds the ledger instead of returning stale data."""
    _sources_cache.clear()
    _ledger_cache.clear()


# ── Helpers ───────────────────────────────────────────────────────────────


def _category_counts(s, source_id: int | None = None, country: str | None = None) -> dict[str, int]:
    """Count classified jobs per core market category, optionally filtered by source and/or country.

    Only counts jobs from ACTIVE sources — deactivating a source immediately drops
    its contributions from every dashboard / stats / sidebar total so the UI
    reflects current board membership.
    """
    from vacancysoft.db.models import ClassificationResult
    q = (
        select(ClassificationResult.primary_taxonomy_key, func.count())
        .join(EnrichedJob, ClassificationResult.enriched_job_id == EnrichedJob.id)
        .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
        .join(Source, RawJob.source_id == Source.id)
        .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
        .where(Source.active.is_(True))
        .where(RawJob.is_deleted_at_source.is_(False))
        .group_by(ClassificationResult.primary_taxonomy_key)
    )
    if source_id is not None:
        q = q.where(RawJob.source_id == source_id)
    if country is not None:
        q = q.where(EnrichedJob.location_country == country)
    raw = dict(s.execute(q).all())
    return {_CATEGORY_LABELS.get(k, k): v for k, v in raw.items()}


def _core_market_total(s, source_id: int | None = None) -> int:
    return sum(_category_counts(s, source_id).values())


def _extract_employer_from_payload(payload) -> str | None:
    """Best-effort extraction of the hiring company's display name from a raw
    aggregator listing payload (Adzuna/Reed/eFinancialCareers/Google Jobs/Coresignal).
    Returns None for anything we can't resolve."""
    if not isinstance(payload, dict):
        return None
    co = payload.get("company")
    if isinstance(co, dict):
        val = co.get("display_name")
        if val and str(val).strip():
            return str(val).strip()
    for key in ("employer_name", "employerName", "companyName", "company_name"):
        val = payload.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def _build_source_card_ledger(session, country: str | None = None) -> list[dict]:
    """Build a canonical per-employer ledger of deduped core-market leads.

    Every lead from an active source that classifies into one of the seven core
    markets is placed on exactly one card, keyed by normalised employer name.
    Direct-source leads take precedence over aggregator-matched leads when
    (employer, title, category) collide. Aggregator leads with no extractable
    employer in their payload are dropped (no card to put them on).

    Returns: list of card dicts, each with keys:
        employer_display, employer_norm, card_id, adapter_name, base_url,
        active, seed_type, ats_family, direct_source_ids (list[int]),
        lead_ids (list[str] — enriched_job_ids, deduped),
        categories (dict[category_label, int]),
        categories_by_country (dict[country, dict[category_label, int]]),
        sub_specialisms (dict[category_label, dict[sub_label, int]]),
        aggregator_hits (dict[str, int]),
        raw_jobs_count (int), last_run_status, last_run_error.

    The same ledger is used by both the card listing endpoint (/api/sources)
    and the card-detail endpoint (/api/sources/{id}/jobs), so card counts and
    detail-view counts are always equal.
    """
    from vacancysoft.db.models import ClassificationResult, SourceRun
    from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category
    routing = load_legacy_routing()

    # ---- 1) Fetch every core-market lead from active sources ----
    # Sub-specialism comes from ClassificationResult.sub_specialism (populated
    # by classify_against_legacy_taxonomy at classify-time). Previously this
    # aggregator called map_sub_specialism() against configs/legacy_routing.yaml,
    # which carried the old pre-reduction taxonomy — leading to the Sources
    # page showing stale chips after the 2026-04-20 taxonomy refactor.
    q = (
        select(
            RawJob.id, RawJob.source_id, RawJob.listing_payload, RawJob.discovered_url,
            EnrichedJob.id, EnrichedJob.title, EnrichedJob.location_country,
            ClassificationResult.primary_taxonomy_key,
            ClassificationResult.sub_specialism,
            ScoreResult.export_eligibility_score,
            Source.id, Source.employer_name, Source.adapter_name, Source.base_url,
            Source.active, Source.seed_type, Source.ats_family,
            ClassificationResult.employment_type,
        )
        .select_from(ClassificationResult)
        .join(EnrichedJob, ClassificationResult.enriched_job_id == EnrichedJob.id)
        .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
        .join(Source, RawJob.source_id == Source.id)
        .outerjoin(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id)
        .where(ClassificationResult.primary_taxonomy_key.in_(_CORE_MARKETS))
        .where(Source.active.is_(True))
        .where(RawJob.is_deleted_at_source.is_(False))
    )
    if country:
        q = q.where(EnrichedJob.location_country == country)
    rows = session.execute(q).all()

    # ---- 2) Dedup by (employer_norm, title_key, category_key); direct wins ties ----
    dedup: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        (_raw_id, _raw_source_id, payload, _url,
         enriched_id, title, loc_country,
         cat_key, sub_spec, _score,
         src_id, src_employer_name, src_adapter, _src_base_url,
         _src_active, _src_seed, _src_ats, employment_type) = r

        is_aggregator = src_adapter in _AGGREGATOR_ADAPTERS
        if is_aggregator:
            employer_display = _extract_employer_from_payload(payload)
            if not employer_display:
                continue  # orphan aggregator lead — no card home
        else:
            employer_display = src_employer_name or ""
            if not employer_display.strip():
                continue
        employer_norm = employer_display.lower().strip()
        title_key = (title or "").lower().strip()[:60]
        dedup_key = (employer_norm, title_key, cat_key)

        record = {
            "enriched_id": enriched_id,
            "title": title or "",
            "cat_key": cat_key,
            "sub_spec": sub_spec,
            "country": loc_country,
            "src_id": src_id,
            "adapter": src_adapter,
            "is_aggregator": is_aggregator,
            "employer_display": employer_display,
            "employer_norm": employer_norm,
            "employment_type": employment_type or "Permanent",
        }
        existing = dedup.get(dedup_key)
        if existing is None:
            dedup[dedup_key] = record
        elif existing["is_aggregator"] and not is_aggregator:
            dedup[dedup_key] = record  # promote to direct
        # else: keep existing (first-seen wins within same tier)

    # ---- 3) Aggregate deduped leads into per-employer cards ----
    cards: dict[str, dict] = {}
    for lead in dedup.values():
        card = cards.setdefault(lead["employer_norm"], {
            "employer_display": lead["employer_display"],
            "employer_norm": lead["employer_norm"],
            "card_id": 0,
            "adapter_name": "",
            "base_url": "",
            "active": True,
            "is_psl": False,
            "seed_type": None,
            "ats_family": None,
            "direct_source_ids": [],
            "lead_ids": [],
            "categories": {},
            "categories_by_country": {},
            "sub_specialisms": {},
            # Country-scoped sub-specialism breakdown so the frontend can
            # narrow chip counts + the grid's sub filter to the active
            # country. The flat `sub_specialisms` blob above is kept for
            # any legacy readers that want the global total.
            "sub_specialisms_by_country": {},
            "aggregator_hits": {},
            "employment_types": {},
            "raw_jobs_count": 0,
            "last_run_status": None,
            "last_run_error": None,
        })
        card["lead_ids"].append(lead["enriched_id"])
        # Use map_category so the label fed into map_sub_specialism matches
        # what the per-job endpoint (GET /api/sources/{id}/jobs) uses —
        # otherwise the card's sub_specialisms blob and the drawer's rows
        # can be computed against different cat labels and disagree.
        cat_label = map_category(lead["cat_key"], routing)
        card["categories"][cat_label] = card["categories"].get(cat_label, 0) + 1
        country_key = lead["country"] or "N/A"
        card["categories_by_country"].setdefault(country_key, {})
        card["categories_by_country"][country_key][cat_label] = (
            card["categories_by_country"][country_key].get(cat_label, 0) + 1
        )
        # Sub-specialism bucket per (category, sub). Read straight from the
        # ClassificationResult.sub_specialism column (populated by the
        # title-taxonomy classifier at classify-time). Fall back to "Other"
        # for rows classified before the sub_specialism column existed
        # (should be zero after the 2026-04-20 reclassify, but kept as a
        # safety net in case a new lead slips through without classification).
        sub_label = lead["sub_spec"] or "Other"
        card["sub_specialisms"].setdefault(cat_label, {})
        card["sub_specialisms"][cat_label][sub_label] = (
            card["sub_specialisms"][cat_label].get(sub_label, 0) + 1
        )
        # Country-scoped copy. Same (cat_label, sub_label) aggregation keyed
        # under the lead's country so the UI can show UK sub counts when the
        # country filter is UK, rather than the global sum.
        card["sub_specialisms_by_country"].setdefault(country_key, {}).setdefault(cat_label, {})
        card["sub_specialisms_by_country"][country_key][cat_label][sub_label] = (
            card["sub_specialisms_by_country"][country_key][cat_label].get(sub_label, 0) + 1
        )
        if lead["is_aggregator"]:
            card["aggregator_hits"][lead["adapter"]] = card["aggregator_hits"].get(lead["adapter"], 0) + 1
        et = lead.get("employment_type") or "Permanent"
        card["employment_types"][et] = card["employment_types"].get(et, 0) + 1

    # ---- 4) Resolve card metadata (card_id, run status, raw_jobs count) ----
    direct_sources = list(session.execute(
        select(Source).where(
            Source.active.is_(True),
            Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS),
        )
    ).scalars())
    direct_by_emp: dict[str, list] = {}
    for src in direct_sources:
        key = (src.employer_name or "").lower().strip()
        direct_by_emp.setdefault(key, []).append(src)

    raw_counts: dict[int, int] = {}
    if direct_sources:
        raw_counts = dict(session.execute(
            select(RawJob.source_id, func.count())
            .where(RawJob.source_id.in_([s.id for s in direct_sources]))
            .where(RawJob.is_deleted_at_source.is_(False))
            .group_by(RawJob.source_id)
        ).all())

    last_runs: dict[int, tuple] = {}
    for src in direct_sources:
        lr = session.execute(
            select(SourceRun.status, SourceRun.diagnostics_blob)
            .where(SourceRun.source_id == src.id)
            .order_by(SourceRun.created_at.desc())
            .limit(1)
        ).first()
        if lr:
            last_runs[src.id] = lr

    virtual_counter = 0
    for card in cards.values():
        matches = direct_by_emp.get(card["employer_norm"], [])
        if matches:
            primary = matches[0]
            card["card_id"] = primary.id
            card["adapter_name"] = primary.adapter_name
            card["base_url"] = primary.base_url or ""
            card["active"] = primary.active
            # PSL flag: True if any of the direct sources backing this
            # card is on the PSL. Operator marks the visible card; the
            # backend stores the flag on the underlying source row.
            card["is_psl"] = any(getattr(m, "is_psl", False) for m in matches)
            card["seed_type"] = primary.seed_type
            card["ats_family"] = primary.ats_family
            card["direct_source_ids"] = [m.id for m in matches]
            card["raw_jobs_count"] = sum(raw_counts.get(m.id, 0) for m in matches)
            # Surface worst run status across all direct sources backing this card
            worst = None
            err = None
            for m in matches:
                lr = last_runs.get(m.id)
                if not lr:
                    continue
                status, diag = lr
                if status in ("error", "FAIL"):
                    worst = status
                    if isinstance(diag, dict):
                        err = diag.get("error") or "Unknown error"
                    break
                if worst is None:
                    worst = status
            card["last_run_status"] = worst
            card["last_run_error"] = err
        else:
            virtual_counter += 1
            card["card_id"] = -virtual_counter
            card["adapter_name"] = "aggregator"
            card["seed_type"] = "aggregator"

    # ---- 5) Inject "genuine empty" cards ----
    # Surface direct sources that have been looked at recently and genuinely
    # returned nothing, cross-checked against aggregator coverage. Criteria:
    #   (a) latest SourceRun for at least one matching direct source is
    #       status="success" AND created_at within the last 24h
    #   (b) total RawJob count across all direct sources for the employer is 0
    #   (c) at least one aggregator has a success SourceRun within 24h, AND
    #       no RawJob from those aggregators mentions the employer_norm in
    #       its payload (via _extract_employer_from_payload, same normalisation).
    # Employers already on a With-Leads card are skipped (precedence).
    # Country-filtered ledgers skip this pass — empty cards have no country.
    if country is None and direct_by_emp:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        # Latest SourceRun per source via grouped subquery
        latest_run_sq = (
            select(
                SourceRun.source_id.label("sid"),
                func.max(SourceRun.created_at).label("mx"),
            )
            .group_by(SourceRun.source_id)
            .subquery()
        )

        fresh_direct_ids: set[int] = set(session.execute(
            select(Source.id)
            .join(latest_run_sq, latest_run_sq.c.sid == Source.id)
            .join(
                SourceRun,
                (SourceRun.source_id == latest_run_sq.c.sid)
                & (SourceRun.created_at == latest_run_sq.c.mx),
            )
            .where(Source.active.is_(True))
            .where(Source.adapter_name.notin_(_AGGREGATOR_ADAPTERS))
            .where(SourceRun.status == "success")
            .where(SourceRun.created_at >= cutoff)
        ).scalars())

        fresh_agg_ids: set[int] = set(session.execute(
            select(Source.id)
            .join(latest_run_sq, latest_run_sq.c.sid == Source.id)
            .join(
                SourceRun,
                (SourceRun.source_id == latest_run_sq.c.sid)
                & (SourceRun.created_at == latest_run_sq.c.mx),
            )
            .where(Source.active.is_(True))
            .where(Source.adapter_name.in_(_AGGREGATOR_ADAPTERS))
            .where(SourceRun.status == "success")
            .where(SourceRun.created_at >= cutoff)
        ).scalars())

        # Short-circuit: cannot confirm aggregator coverage → no injection.
        if fresh_agg_ids:
            agg_matched_norms: set[str] = set()
            for (payload,) in session.execute(
                select(RawJob.listing_payload)
                .where(RawJob.source_id.in_(fresh_agg_ids))
                .where(RawJob.first_seen_at >= cutoff)
                .where(RawJob.is_deleted_at_source.is_(False))
            ):
                name = _extract_employer_from_payload(payload)
                if name:
                    norm = name.lower().strip()
                    if norm:
                        agg_matched_norms.add(norm)

            for employer_norm, matches in direct_by_emp.items():
                if not employer_norm or employer_norm in cards:
                    continue
                if not any(m.id in fresh_direct_ids for m in matches):
                    continue
                if sum(raw_counts.get(m.id, 0) for m in matches) != 0:
                    continue
                if employer_norm in agg_matched_norms:
                    continue
                primary = matches[0]
                # Reuse the worst-status loop from step 4 so sibling FAILs surface.
                worst = None
                err = None
                for m in matches:
                    lr = last_runs.get(m.id)
                    if not lr:
                        continue
                    status, diag = lr
                    if status in ("error", "FAIL"):
                        worst = status
                        if isinstance(diag, dict):
                            err = diag.get("error") or "Unknown error"
                        break
                    if worst is None:
                        worst = status
                cards[employer_norm] = {
                    "employer_display": primary.employer_name or "",
                    "employer_norm": employer_norm,
                    "card_id": primary.id,
                    "adapter_name": primary.adapter_name,
                    "base_url": primary.base_url or "",
                    "active": primary.active,
                    "seed_type": primary.seed_type,
                    "ats_family": primary.ats_family,
                    "direct_source_ids": [m.id for m in matches],
                    "lead_ids": [],
                    "categories": {},
                    "categories_by_country": {},
                    "sub_specialisms": {},
                    "sub_specialisms_by_country": {},
                    "aggregator_hits": {},
                    "employment_types": {},
                    "raw_jobs_count": 0,
                    "last_run_status": worst,
                    "last_run_error": err,
                }

    return sorted(cards.values(), key=lambda c: len(c["lead_ids"]), reverse=True)


def _get_cached_ledger(country: str | None = None) -> list[dict]:
    """Ledger cache shared across /api/sources and /api/sources/{id}/jobs."""
    import time as _t
    key = country or "__all__"
    cached = _ledger_cache.get(key)
    if cached and (_t.time() - cached[0]) < _SOURCES_CACHE_TTL:
        return cached[1]
    with SessionLocal() as s:
        ledger = _build_source_card_ledger(s, country=country)
    _ledger_cache[key] = (_t.time(), ledger)
    return ledger
