"""ARQ worker tasks for dossier generation and source scraping."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select, func

load_dotenv()

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import EnrichedJob, RawJob, ReviewQueueItem, Source
from vacancysoft.intelligence.url_scrape import scrape_advert

logger = logging.getLogger(__name__)


async def process_lead(ctx: dict[str, Any], item_id: str, url: str | None, company: str | None, title: str | None) -> None:
    """Scrape job advert and generate intelligence dossier.

    This runs in the ARQ worker process, not the API server.
    Up to 25 of these can run concurrently (configured in WorkerSettings.max_jobs).
    """
    try:
        with SessionLocal() as s:
            item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
            if not item:
                logger.warning("Queue item %s not found, skipping", item_id)
                return

            item.status = "generating"
            s.commit()

            # The enriched job is named directly on the queue item — no
            # fuzzy URL / title match needed. Every caller that creates a
            # ReviewQueueItem populates enriched_job_id (see
            # api/routes/leads.py + cli/app.py).
            enriched = s.execute(
                select(EnrichedJob).where(EnrichedJob.id == item.enriched_job_id)
            ).scalar_one_or_none()

            if not enriched:
                logger.warning(
                    "Queue item %s references missing enriched_job_id %s — reverting to pending",
                    item_id, item.enriched_job_id,
                )
                item.status = "pending"
                s.commit()
                return

            # Step 1: If no description, scrape it via the Playwright runner
            if not (enriched.description_text or "").strip() and url:
                workday_cfg: dict | None = None
                raw = s.get(RawJob, enriched.raw_job_id)
                if raw:
                    src = s.get(Source, raw.source_id)
                    if src and src.adapter_name == "workday":
                        config = src.config_blob or {}
                        if config.get("tenant") and config.get("shard") and config.get("site_path"):
                            workday_cfg = {
                                "tenant": config["tenant"],
                                "shard": config["shard"],
                                "sitePath": config["site_path"],
                            }

                meta = await scrape_advert(url, workday=workday_cfg)
                description = (meta.get("description") or "").strip()
                if description and meta.get("status") in ("success", "empty"):
                    enriched.description_text = description
                    s.commit()
                    logger.info("Scraped description for %s (%d chars)", url, len(description))
                elif meta.get("status") == "error":
                    logger.warning("Scrape failed for %s: %s", url, meta.get("error"))

            # Step 2: Generate dossier
            from vacancysoft.intelligence.dossier import generate_dossier
            dossier = await generate_dossier(enriched.id, s)
            logger.info(
                "Dossier complete: %s at %s — score=%s, %d tokens, %dms",
                title, company, dossier.lead_score, dossier.tokens_used or 0, dossier.latency_ms or 0,
            )

            # Step 3: Pre-generate the campaign so the Builder loads from cache
            # with no user-visible wait. Failure here is non-fatal: the lead
            # still becomes ready; the Builder will fall back to on-demand
            # generation if the cache miss happens.
            try:
                from vacancysoft.intelligence.campaign import generate_campaign
                campaign = await generate_campaign(dossier.id, s)
                logger.info(
                    "Campaign pre-generated: %s at %s — %d emails, %d tokens, %dms",
                    title, company,
                    len(campaign.outreach_emails or []),
                    campaign.tokens_used or 0,
                    campaign.latency_ms or 0,
                )
            except Exception as camp_exc:
                logger.warning(
                    "Campaign pre-generation failed for %s (%s at %s) — "
                    "lead will still be marked ready; Builder will regenerate on demand: %s",
                    item_id, title, company, camp_exc,
                )

            # Step 4: Update status to ready
            item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
            if item:
                item.status = "ready"
                s.commit()

    except Exception as exc:
        logger.error("Dossier generation failed for %s: %s", item_id, exc, exc_info=True)
        try:
            with SessionLocal() as s:
                item = s.execute(select(ReviewQueueItem).where(ReviewQueueItem.id == item_id)).scalar_one_or_none()
                if item:
                    item.status = "pending"
                    s.commit()
        except Exception:
            pass
        raise  # Let ARQ handle retry


async def scrape_source(ctx: dict[str, Any], source_id: int) -> None:
    """Scrape a source: discover jobs, enrich, classify, score.

    Runs in the ARQ worker so multiple sources can be scraped concurrently.
    """
    from vacancysoft.adapters import ADAPTER_REGISTRY
    from vacancysoft.pipelines.persistence import persist_discovery_batch
    from vacancysoft.db.models import SourceRun

    try:
        with SessionLocal() as s:
            src = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
            if not src:
                logger.warning("Source %d not found, skipping", source_id)
                return

            source_key = src.source_key
            employer_name = src.employer_name
            adapter_name = src.adapter_name
            config = dict(src.config_blob or {})
            config.setdefault("company", src.employer_name)
            if src.base_url and config.get("job_board_url") != src.base_url:
                config["job_board_url"] = src.base_url

        adapter_cls = ADAPTER_REGISTRY.get(adapter_name)
        if not adapter_cls:
            logger.warning("No adapter for %s (%s), skipping", employer_name, adapter_name)
            return

        adapter = adapter_cls()
        result = await asyncio.wait_for(adapter.discover(config), timeout=300)
        jobs_found = len(result.jobs) if result and result.jobs else 0

        if result and result.jobs:
            with SessionLocal() as s:
                source_obj = s.execute(select(Source).where(Source.source_key == source_key)).scalar_one()
                persist_discovery_batch(session=s, source=source_obj, records=result.jobs, trigger="api_scrape")

        if jobs_found > 0:
            from vacancysoft.pipelines.enrichment_persistence import enrich_raw_jobs
            from vacancysoft.pipelines.classification_persistence import classify_enriched_jobs
            from vacancysoft.pipelines.scoring_persistence import score_enriched_jobs
            with SessionLocal() as s:
                enrich_raw_jobs(s, limit=None)
            with SessionLocal() as s:
                classify_enriched_jobs(s, limit=None)
            with SessionLocal() as s:
                score_enriched_jobs(s, limit=None)

        # Deactivate older duplicate sources with 0 jobs
        if jobs_found > 0:
            with SessionLocal() as s:
                src_obj = s.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()
                if src_obj:
                    dupes = list(s.execute(
                        select(Source).where(
                            Source.employer_name == employer_name,
                            Source.id != src_obj.id,
                            Source.active.is_(True),
                        )
                    ).scalars())
                    for dupe in dupes:
                        raw_count = s.execute(
                            select(func.count()).select_from(RawJob).where(RawJob.source_id == dupe.id)
                        ).scalar() or 0
                        if raw_count == 0:
                            dupe.active = False
                    s.commit()

        with SessionLocal() as s:
            src_obj = s.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()
            if src_obj:
                s.add(SourceRun(source_id=src_obj.id, run_type="discovery", status="success",
                    trigger="api_scrape", raw_jobs_created=jobs_found,
                    diagnostics_blob={"jobs_found": jobs_found}))
                s.commit()

        logger.info("Scrape complete: %s (%s) — %d jobs", employer_name, adapter_name, jobs_found)

    except asyncio.TimeoutError:
        logger.warning("Scrape timeout: %s", source_id)
        with SessionLocal() as s:
            from vacancysoft.db.models import SourceRun
            src_obj = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
            if src_obj:
                s.add(SourceRun(source_id=src_obj.id, run_type="discovery", status="error",
                    trigger="api_scrape", diagnostics_blob={"error": "Timeout"}))
                s.commit()
        raise

    except Exception as exc:
        logger.error("Scrape failed for source %d: %s", source_id, exc, exc_info=True)
        with SessionLocal() as s:
            from vacancysoft.db.models import SourceRun
            src_obj = s.execute(select(Source).where(Source.id == source_id)).scalar_one_or_none()
            if src_obj:
                s.add(SourceRun(source_id=src_obj.id, run_type="discovery", status="error",
                    trigger="api_scrape", diagnostics_blob={"error": str(exc)[:200]}))
                s.commit()
        raise
