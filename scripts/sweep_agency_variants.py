#!/usr/bin/env python3
"""Retroactive sweep: hard-delete EnrichedJobs whose company is a
decorated variant of an agency in configs/agency_exclusions.yaml.

Why
---
Until 2026-04-30, ``is_recruiter()`` only matched runtime YAML
entries by exact lowercase string equality. So clicking "Agy Job" on
"Korn Ferry" would write ``korn ferry`` to the YAML and delete the
EnrichedJobs that exactly matched, but the next aggregator scrape
might extract "Korn Ferry International" as the employer — that
slipped through the filter and the row reappeared on the Live Feed.

PR (this commit) added a token-subset matcher so future enrichments
catch the variants. This script runs the same matcher across the
existing dataset to clean up the backlog of variants that were
created before the fix.

Scope: every ``EnrichedJob`` whose ``team`` (lowercased) is a
token-superset of any YAML-listed agency name. Same cascade as
``/api/agency``: classification_results, score_results,
intelligence_dossiers, campaign_outputs, review_queue_items,
enriched_jobs. RawJobs and Sources are left intact (re-enrichment
under the new matcher will mark them ``agency_filtered``).

Usage
-----
  python3 scripts/sweep_agency_variants.py             # dry run
  python3 scripts/sweep_agency_variants.py --commit    # actually delete
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import (
    CampaignOutput,
    ClassificationResult,
    EnrichedJob,
    IntelligenceDossier,
    RawJob,
    ReviewQueueItem,
    ScoreResult,
    Source,
)
from vacancysoft.db.session import SessionLocal
from vacancysoft.enrichers.recruiter_filter import _RUNTIME_EXCLUSIONS


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str | None) -> set[str]:
    if not s:
        return set()
    return set(_TOKEN_RE.findall(s.lower()))


def _find_variant_eids(session: Session) -> dict[str, set[str]]:
    """Return {agency_entry: {enriched_job_id, ...}}.

    Iterates every EnrichedJob with a non-null ``team`` and matches
    against each YAML entry by token subset. Also catches direct sources
    where Source.employer_name itself is a variant.
    """
    if not _RUNTIME_EXCLUSIONS:
        return {}

    entry_tokens: dict[str, set[str]] = {
        entry: _tokens(entry) for entry in _RUNTIME_EXCLUSIONS
    }
    # Drop entries with no usable tokens (defensive — shouldn't happen).
    entry_tokens = {k: v for k, v in entry_tokens.items() if v}

    matches: dict[str, set[str]] = defaultdict(set)

    # Match via EnrichedJob.team
    rows = session.execute(
        select(EnrichedJob.id, EnrichedJob.team).where(EnrichedJob.team.is_not(None))
    ).all()
    for ej_id, team in rows:
        cand = _tokens(team)
        if not cand:
            continue
        for entry, etoks in entry_tokens.items():
            if etoks <= cand:
                # Skip exact match (already handled by the original /api/agency).
                if team.strip().lower() == entry:
                    continue
                matches[entry].add(ej_id)

    # Match via Source.employer_name (catches direct sources).
    src_rows = session.execute(
        select(Source.id, Source.employer_name).where(Source.employer_name.is_not(None))
    ).all()
    matched_sources: dict[str, set[str]] = defaultdict(set)
    for src_id, emp in src_rows:
        cand = _tokens(emp)
        if not cand:
            continue
        for entry, etoks in entry_tokens.items():
            if etoks <= cand and emp.strip().lower() != entry:
                matched_sources[entry].add(src_id)

    # Translate matched sources → enriched_job_ids
    for entry, src_ids in matched_sources.items():
        if not src_ids:
            continue
        ejs = session.execute(
            select(EnrichedJob.id)
            .join(RawJob, EnrichedJob.raw_job_id == RawJob.id)
            .where(RawJob.source_id.in_(list(src_ids)))
        ).scalars()
        for ej_id in ejs:
            matches[entry].add(ej_id)

    return dict(matches)


def _hard_delete_enriched_jobs(session: Session, ej_ids: set[str]) -> dict[str, int]:
    """Mirror the cascade from /api/agency."""
    if not ej_ids:
        return {"jobs": 0, "classifications": 0, "scores": 0, "dossiers": 0, "queue": 0, "campaigns": 0}
    ej_list = list(ej_ids)

    # Look up dossier ids first so we can delete dependent campaign_outputs.
    dossier_ids = list(session.execute(
        select(IntelligenceDossier.id)
        .where(IntelligenceDossier.enriched_job_id.in_(ej_list))
    ).scalars())

    deleted_campaigns = 0
    if dossier_ids:
        deleted_campaigns = session.execute(
            sa_delete(CampaignOutput).where(CampaignOutput.dossier_id.in_(dossier_ids))
        ).rowcount or 0

    chunk_size = 500
    deleted_dossiers = deleted_queue = deleted_scores = deleted_class = deleted_jobs = 0
    for i in range(0, len(ej_list), chunk_size):
        chunk = ej_list[i : i + chunk_size]
        deleted_dossiers += session.execute(
            sa_delete(IntelligenceDossier).where(IntelligenceDossier.enriched_job_id.in_(chunk))
        ).rowcount or 0
        deleted_queue += session.execute(
            sa_delete(ReviewQueueItem).where(ReviewQueueItem.enriched_job_id.in_(chunk))
        ).rowcount or 0
        deleted_scores += session.execute(
            sa_delete(ScoreResult).where(ScoreResult.enriched_job_id.in_(chunk))
        ).rowcount or 0
        deleted_class += session.execute(
            sa_delete(ClassificationResult).where(ClassificationResult.enriched_job_id.in_(chunk))
        ).rowcount or 0
        deleted_jobs += session.execute(
            sa_delete(EnrichedJob).where(EnrichedJob.id.in_(chunk))
        ).rowcount or 0

    return {
        "jobs": deleted_jobs,
        "classifications": deleted_class,
        "scores": deleted_scores,
        "dossiers": deleted_dossiers,
        "queue": deleted_queue,
        "campaigns": deleted_campaigns,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Apply changes.")
    args = parser.parse_args()

    print(f"Loaded {len(_RUNTIME_EXCLUSIONS)} runtime YAML exclusions.")

    with SessionLocal() as session:
        matches = _find_variant_eids(session)

        if not matches:
            print("No decorated variants found in the DB. Nothing to do.")
            return 0

        # Print breakdown.
        total = sum(len(s) for s in matches.values())
        print(f"\nFound {total} variant EnrichedJob rows across {len(matches)} agencies:\n")
        for entry in sorted(matches.keys()):
            print(f"  {entry:<40} {len(matches[entry]):>5}")

        if not args.commit:
            print("\n[DRY RUN] Pass --commit to actually delete.")
            return 0

        all_ids: set[str] = set()
        for ids in matches.values():
            all_ids |= ids

        counts = _hard_delete_enriched_jobs(session, all_ids)
        session.commit()

        print(
            "\nDeleted: "
            + ", ".join(f"{k}={v}" for k, v in counts.items())
        )

        # Drop dashboard / ledger caches if the API process is in the same
        # interpreter (it usually isn't — the API runs separately). The
        # cache is a 30s TTL anyway, so this is just a nice-to-have.
        try:
            from vacancysoft.api.ledger import clear_ledger_caches
            from vacancysoft.api.routes.leads import clear_dashboard_cache
            clear_ledger_caches()
            clear_dashboard_cache()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
