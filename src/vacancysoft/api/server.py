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
    try:
        app.state.redis = await create_pool(_redis_settings())
        # Re-enqueue any orphaned pending leads from previous server runs
        from vacancysoft.db.models import ReviewQueueItem
        with SessionLocal() as s:
            # Fix leads stuck in "generating" that already have dossiers
            from vacancysoft.db.models import IntelligenceDossier
            stuck = list(s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.status == "generating")
                .where(ReviewQueueItem.queue_type == "campaign")
                .where(ReviewQueueItem.enriched_job_id.in_(
                    select(IntelligenceDossier.enriched_job_id)
                ))
            ).scalars())
            for item in stuck:
                item.status = "ready"
            if stuck:
                s.commit()

            # Re-enqueue genuinely pending/stuck leads
            pending = list(s.execute(
                select(ReviewQueueItem)
                .where(ReviewQueueItem.status.in_(("pending", "generating")))
                .where(ReviewQueueItem.queue_type == "campaign")
            ).scalars())
            for item in pending:
                ev = item.evidence_blob or {}
                await app.state.redis.enqueue_job("process_lead", item.id, ev.get("url"), ev.get("company"), ev.get("title"))
            if pending or stuck:
                import logging
                logging.getLogger(__name__).info("Startup: fixed %d stuck leads, re-enqueued %d pending", len(stuck), len(pending))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Redis not available, falling back to in-process tasks: %s", exc)
        app.state.redis = None

@app.on_event("shutdown")
async def _shutdown():
    if getattr(app.state, "redis", None):
        await app.state.redis.close()




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
