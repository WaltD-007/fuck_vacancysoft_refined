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


# ── Redis connection for ARQ job queue ──
from arq import create_pool
from vacancysoft.worker.settings import _redis_settings

@app.on_event("startup")
async def _startup():
    import logging
    logger = logging.getLogger(__name__)

    # 1) Connect to Redis. If this fails, fall back to in-process tasks
    #    (queue_campaign uses asyncio.ensure_future when app.state.redis
    #    is None) and return — can't self-heal without a Redis pool.
    try:
        app.state.redis = await create_pool(_redis_settings())
    except Exception as exc:
        logger.warning("Redis not available, falling back to in-process tasks: %s", exc)
        app.state.redis = None
        return

    # 2) Self-heal: sweep the queue for any ReviewQueueItem still in
    #    "pending" / "generating" state and push it back onto ARQ. Catches
    #    items that were written to the DB while Redis was briefly down
    #    (e.g. the UOB case on 2026-04-19). Separate try so a self-heal
    #    hiccup doesn't flip app.state.redis back to None — the pool is
    #    fine; only the sweep failed.
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
