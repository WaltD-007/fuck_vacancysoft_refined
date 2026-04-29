"""Prospero API — lightweight FastAPI server wrapping the scraping engine."""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from vacancysoft.api.routes import add_company as add_company_routes
from vacancysoft.api.routes import campaigns as campaigns_routes
from vacancysoft.api.routes import leads as leads_routes
from vacancysoft.api.routes import sources as sources_routes
from vacancysoft.api.routes import tracking as tracking_routes
from vacancysoft.api.routes import users as users_routes
from vacancysoft.api.routes import voice as voice_routes
from vacancysoft.db.engine import SessionLocal

app = FastAPI(title="Prospero API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Per-topic route modules ──
app.include_router(leads_routes.router)
app.include_router(sources_routes.router)
app.include_router(add_company_routes.router)
app.include_router(campaigns_routes.router)
app.include_router(users_routes.router)
app.include_router(voice_routes.router)
# Tracking endpoints — anonymous on purpose (recipients' mail clients
# hit them, not logged-in users). When Easy Auth lands, /t/* MUST be
# carved out of the auth-required path list.
app.include_router(tracking_routes.router)


# ── Redis connection for ARQ job queue ──
from arq import create_pool
from vacancysoft.worker.settings import _redis_settings

async def _warm_caches() -> None:
    """Prime the in-memory caches in the background so the first Dashboard
    and Sources hits don't pay the full cold-query cost. Runs off the
    event loop via asyncio.to_thread — the underlying queries are synchronous
    SQLAlchemy work."""
    import asyncio
    import logging
    import time
    logger = logging.getLogger(__name__)

    def _prime() -> None:
        t0 = time.time()
        try:
            from vacancysoft.api.ledger import _get_cached_ledger
            _get_cached_ledger(country=None)
            logger.info("Warmed ledger cache in %.2fs", time.time() - t0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cache warmup failed (ledger): %s", exc)
        t1 = time.time()
        try:
            from vacancysoft.api.routes.leads import get_dashboard
            get_dashboard()
            logger.info("Warmed dashboard cache in %.2fs", time.time() - t1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cache warmup failed (dashboard): %s", exc)

    try:
        await asyncio.to_thread(_prime)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cache warmup task crashed: %s", exc)


@app.on_event("startup")
async def _startup():
    import asyncio
    import logging
    logger = logging.getLogger(__name__)

    # 1) Connect to Redis. If this fails, fall back to in-process tasks
    #    (queue_campaign uses asyncio.ensure_future when app.state.redis
    #    is None) — can't self-heal without a Redis pool, but cache warmup
    #    is independent of Redis so we still kick it off.
    try:
        app.state.redis = await create_pool(_redis_settings())
    except Exception as exc:
        logger.warning("Redis not available, falling back to in-process tasks: %s", exc)
        app.state.redis = None

    # 2) Kick off cache warming in the background so /api/dashboard and
    #    /api/sources are primed before the user's first page load. Non-blocking:
    #    API accepts traffic immediately and the task just populates the
    #    module-level caches when it's done.
    asyncio.create_task(_warm_caches())

    # 3) Self-heal: sweep the queue for any ReviewQueueItem still in
    #    "pending" / "generating" state and push it back onto ARQ. Catches
    #    items that were written to the DB while Redis was briefly down
    #    (e.g. the UOB case on 2026-04-19). Separate try so a self-heal
    #    hiccup doesn't flip app.state.redis back to None — the pool is
    #    fine; only the sweep failed.
    if app.state.redis is None:
        return
    try:
        from vacancysoft.worker.self_heal import reenqueue_pending_leads
        await reenqueue_pending_leads(app.state.redis)
    except Exception as exc:
        logger.error("Self-heal at API startup failed: %s", exc, exc_info=True)
        # Don't propagate — API should still serve even if self-heal flunks.

@app.on_event("shutdown")
async def _shutdown():
    if getattr(app.state, "redis", None):
        await app.state.redis.close()




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
