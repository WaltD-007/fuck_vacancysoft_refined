"""ARQ worker settings.

Run the worker with:
    arq vacancysoft.worker.settings.WorkerSettings
"""

from __future__ import annotations

import logging
import os
import tomllib

from arq.connections import RedisSettings
from dotenv import load_dotenv

load_dotenv()


def _load_worker_config() -> dict:
    try:
        with open("configs/app.toml", "rb") as f:
            return tomllib.load(f).get("worker", {})
    except Exception:
        return {}


def _redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        config = _load_worker_config()
        url = config.get("redis_url", "redis://localhost:6379")

    # Parse redis://host:port
    url = url.replace("redis://", "")
    parts = url.split(":")
    host = parts[0] or "localhost"
    port = int(parts[1]) if len(parts) > 1 else 6379
    return RedisSettings(host=host, port=port)


config = _load_worker_config()


class WorkerSettings:
    """ARQ worker configuration."""

    from vacancysoft.worker.tasks import process_lead, scrape_source
    functions = [process_lead, scrape_source]

    redis_settings = _redis_settings()
    max_jobs = config.get("max_concurrent", 25)
    job_timeout = config.get("job_timeout", 300)
    max_tries = config.get("max_retries", 3)
    retry_jobs = True
    health_check_interval = 30

    # Logging + self-heal
    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        logging.getLogger("vacancysoft").setLevel(logging.INFO)
        logger = logging.getLogger(__name__)
        logger.info(
            "Worker started — max_jobs=%d, job_timeout=%ds, max_tries=%d",
            WorkerSettings.max_jobs, WorkerSettings.job_timeout, WorkerSettings.max_tries,
        )

        # Self-heal: sweep for any ReviewQueueItem still in pending /
        # generating and push it back onto ARQ. Matching behaviour to
        # api/server.py::_startup so whichever service (API or worker)
        # boots first catches any stuck items. Uses _job_id dedup so a
        # re-run on an already-queued item is a no-op.
        try:
            from vacancysoft.worker.self_heal import reenqueue_pending_leads
            await reenqueue_pending_leads(ctx["redis"])
        except Exception as exc:
            logger.warning("Worker self-heal failed: %s", exc, exc_info=True)

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logging.getLogger(__name__).info("Worker shutting down")
