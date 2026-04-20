"""Queue self-heal: re-enqueue ReviewQueueItems stuck in pending / generating.

Called automatically at two points:
  - ``api/server.py::_startup`` (once Redis pool is available)
  - ``worker/settings.py::WorkerSettings.on_startup`` (worker boots)

Belt-and-braces: running the same sweep on both services means a stuck
item gets picked up whichever one restarts next. The caller doesn't
need to pick — whoever boots first catches it.

Recovers from the failure mode we hit on 2026-04-19 with the UOB lead:
the API server started while Redis was still booting under
``start.sh``; ``app.state.redis`` was therefore ``None`` when the
operator clicked "Queue campaign"; the ``queue_campaign`` endpoint
silently used its ``asyncio.ensure_future`` fallback; that in-process
task died when the API process was later restarted; the DB row was
left at ``status="pending"`` with no corresponding ARQ job in Redis.

Idempotent via ``_job_id`` dedup — a re-run will no-op on items
that are already queued (same item_id). Safe to call as often as you
like.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from vacancysoft.db.engine import SessionLocal
from vacancysoft.db.models import IntelligenceDossier, ReviewQueueItem

logger = logging.getLogger(__name__)


async def reenqueue_pending_leads(redis_pool: Any) -> dict[str, int]:
    """Sweep for stuck ReviewQueueItems and push them back onto ARQ.

    Two passes over the queue table:

    1. **generating → ready (if dossier already landed)**: items that
       crashed out after committing the dossier but before the worker
       could flip status to "ready". DB cleanup only; no Redis call.

    2. **pending / generating → re-enqueue**: everything else still in
       flight according to the DB. For each, push a fresh ARQ job onto
       the ``process_lead`` queue via ``redis_pool.enqueue_job``. Uses
       ``_job_id=f"process_lead:{item_id}"`` for de-duplication so an
       item already running (or whose previous run's result is still
       within ARQ's result-keep TTL, default 24h) is a no-op — returns
       ``None`` from ``enqueue_job``.

    :param redis_pool: An ArqRedis instance. Can be either
        ``app.state.redis`` (API side) or ``ctx["redis"]`` (worker
        side) — the shape is identical.

    :returns: Counts dict with keys ``enqueued``, ``already_queued``,
        ``fixed_to_ready``, ``failed``. Useful for tests; the
        corresponding summary is also emitted to the logger.
    """
    stats = {"enqueued": 0, "already_queued": 0, "fixed_to_ready": 0, "failed": 0}

    with SessionLocal() as s:
        # ── Pass 1: generating → ready if dossier already exists ──────────
        stuck_with_dossier = s.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.status == "generating")
            .where(ReviewQueueItem.queue_type == "campaign")
            .where(ReviewQueueItem.enriched_job_id.in_(
                select(IntelligenceDossier.enriched_job_id)
            ))
        ).scalars().all()
        for item in stuck_with_dossier:
            item.status = "ready"
            stats["fixed_to_ready"] += 1
        if stats["fixed_to_ready"]:
            s.commit()

        # ── Pass 2: pending / generating → re-enqueue ─────────────────────
        pending = s.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.status.in_(("pending", "generating")))
            .where(ReviewQueueItem.queue_type == "campaign")
        ).scalars().all()

        for item in pending:
            ev = item.evidence_blob or {}
            try:
                job = await redis_pool.enqueue_job(
                    "process_lead",
                    item.id,
                    ev.get("url"),
                    ev.get("company"),
                    ev.get("title"),
                    _job_id=f"process_lead:{item.id}",
                )
                if job is None:
                    stats["already_queued"] += 1
                else:
                    stats["enqueued"] += 1
            except Exception as exc:
                logger.warning(
                    "Self-heal: failed to re-enqueue %s (%s / %s): %s",
                    item.id, ev.get("company"), ev.get("title"), exc,
                )
                stats["failed"] += 1

    if any(v > 0 for v in stats.values()):
        logger.info(
            "Self-heal swept queue: enqueued=%d already_queued=%d "
            "fixed_to_ready=%d failed=%d",
            stats["enqueued"], stats["already_queued"],
            stats["fixed_to_ready"], stats["failed"],
        )

    return stats
