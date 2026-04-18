from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from vacancysoft.db.models import ExportRecord
from vacancysoft.exporters.json_exporter import build_profile_payload, build_segment_payload
from vacancysoft.exporters.serialisers import build_legacy_webhook_payload
from vacancysoft.exporters.views import fetch_rows, load_exporter_config, new_leads_only_query

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = (2, 5, 15)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _resolve_webhook_url(explicit_url: str | None, config: dict) -> str:
    """Resolve webhook URL: explicit param > env var > config file."""
    if explicit_url:
        return explicit_url
    env_url = os.getenv("N8N_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url
    return config.get("webhook", {}).get("production_url", "")


def _post_with_retry(
    url: str,
    payload: dict,
    timeout_seconds: float,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response:
    """POST with exponential backoff retry on transient failures."""
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(url, json=payload, timeout=timeout_seconds)

            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return response

            if attempt < max_retries:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning(
                    "Webhook returned %d, retrying in %ds (attempt %d/%d)",
                    response.status_code, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            else:
                response.raise_for_status()
                return response

        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Webhook timed out, retrying in %ds: %s", wait, exc)
                time.sleep(wait)
            else:
                raise

        except httpx.ConnectError as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Webhook connection failed, retrying in %ds: %s", wait, exc)
                time.sleep(wait)
            else:
                raise

    raise last_exc or RuntimeError("Webhook retry exhausted with no response")


def _stamp_export_records(
    session: Session,
    enriched_job_ids: list[str],
    batch_id: str,
    destination: str,
    export_view: str,
    delivered: bool,
    status_code: int | None = None,
    response_text: str | None = None,
) -> int:
    """Create ExportRecord entries for each exported lead."""
    now = datetime.utcnow()
    count = 0
    for ej_id in enriched_job_ids:
        record = ExportRecord(
            export_batch_id=batch_id,
            enriched_job_id=ej_id,
            export_view=export_view,
            destination=destination,
            eligibility_decision="accepted_or_review",
            payload_hash="",
            delivered=delivered,
            delivered_at=now if delivered else None,
            delivery_status=str(status_code) if status_code else ("failed" if not delivered else "ok"),
            delivery_response_blob={"response_text": response_text[:500]} if response_text else None,
        )
        session.add(record)
        count += 1
    session.commit()
    return count


def send_new_leads_to_webhook(
    session: Session,
    limit: int = 50000,
    webhook_url: str | None = None,
    dry_run: bool = False,
    destination: str = "webhook",
) -> dict[str, Any]:
    """Send only NEW leads (not previously exported) to the webhook."""
    config = load_exporter_config()
    target_url = _resolve_webhook_url(webhook_url, config)
    timeout_seconds = float(config.get("webhook", {}).get("timeout_seconds", 20))

    # Query only leads not yet exported
    stmt = new_leads_only_query(destination=destination)
    rows = fetch_rows(session, stmt, limit=limit)

    if not rows:
        return {"ok": True, "dry_run": dry_run, "url": target_url, "job_count": 0, "message": "No new leads to send"}

    # Extract enriched_job_ids for stamping
    enriched_job_ids = [row.enriched_job_id for row in rows]
    payload = build_legacy_webhook_payload(rows)
    batch_id = f"webhook_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "url": target_url,
            "job_count": len(payload.get("body", [])),
            "batch_id": batch_id,
            "message": f"{len(enriched_job_ids)} new leads would be sent",
        }

    try:
        response = _post_with_retry(target_url, payload, timeout_seconds)
        # Stamp as delivered
        stamped = _stamp_export_records(
            session, enriched_job_ids, batch_id, destination,
            export_view="new_leads", delivered=True,
            status_code=response.status_code, response_text=response.text[:500],
        )
        return {
            "ok": True,
            "dry_run": False,
            "url": target_url,
            "status_code": response.status_code,
            "job_count": len(payload.get("body", [])),
            "stamped": stamped,
            "batch_id": batch_id,
            "response_text": response.text[:1000],
        }
    except Exception as exc:
        # Stamp as failed so we retry next run
        logger.error("Webhook failed after retries: %s", exc)
        return {
            "ok": False,
            "dry_run": False,
            "url": target_url,
            "job_count": len(payload.get("body", [])),
            "batch_id": batch_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


# ── Legacy functions (send ALL leads, not just new) ──────────────────────

def send_profile_to_webhook(
    session: Session,
    profile_name: str,
    limit: int = 100,
    webhook_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_exporter_config()
    target_url = _resolve_webhook_url(webhook_url, config)
    timeout_seconds = float(config.get("webhook", {}).get("timeout_seconds", 20))
    payload = build_profile_payload(session=session, profile_name=profile_name, limit=limit)

    if dry_run:
        return {
            "ok": True, "dry_run": True, "url": target_url,
            "job_count": len(payload.get("body", [])), "payload": payload,
        }

    try:
        response = _post_with_retry(target_url, payload, timeout_seconds)
        return {
            "ok": True, "dry_run": False, "url": target_url,
            "status_code": response.status_code,
            "job_count": len(payload.get("body", [])),
            "response_text": response.text[:1000],
        }
    except Exception as exc:
        logger.error("Webhook failed after retries: %s", exc)
        return {
            "ok": False, "dry_run": False, "url": target_url,
            "job_count": len(payload.get("body", [])),
            "error": f"{type(exc).__name__}: {exc}",
        }


def send_segment_to_webhook(
    session: Session,
    segment_name: str,
    limit: int = 100,
    webhook_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_exporter_config()
    target_url = _resolve_webhook_url(webhook_url, config)
    timeout_seconds = float(config.get("webhook", {}).get("timeout_seconds", 20))
    payload = build_segment_payload(session=session, segment_name=segment_name, limit=limit)

    if dry_run:
        return {
            "ok": True, "dry_run": True, "url": target_url,
            "job_count": len(payload.get("body", [])), "payload": payload,
        }

    try:
        response = _post_with_retry(target_url, payload, timeout_seconds)
        return {
            "ok": True, "dry_run": False, "url": target_url,
            "status_code": response.status_code,
            "job_count": len(payload.get("body", [])),
            "response_text": response.text[:1000],
        }
    except Exception as exc:
        logger.error("Webhook failed after retries: %s", exc)
        return {
            "ok": False, "dry_run": False, "url": target_url,
            "job_count": len(payload.get("body", [])),
            "error": f"{type(exc).__name__}: {exc}",
        }
