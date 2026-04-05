from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from vacancysoft.exporters.json_exporter import build_profile_payload, build_segment_payload
from vacancysoft.exporters.views import load_exporter_config


def send_profile_to_webhook(
    session: Session,
    profile_name: str,
    limit: int = 100,
    webhook_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_exporter_config()
    target_url = webhook_url or config.get("webhook", {}).get("production_url", "")
    timeout_seconds = float(config.get("webhook", {}).get("timeout_seconds", 20))
    payload = build_profile_payload(session=session, profile_name=profile_name, limit=limit)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "url": target_url,
            "job_count": len(payload.get("body", [])),
            "payload": payload,
        }

    response = httpx.post(target_url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    return {
        "ok": True,
        "dry_run": False,
        "url": target_url,
        "status_code": response.status_code,
        "job_count": len(payload.get("body", [])),
        "response_text": response.text[:1000],
    }


def send_segment_to_webhook(
    session: Session,
    segment_name: str,
    limit: int = 100,
    webhook_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_exporter_config()
    target_url = webhook_url or config.get("webhook", {}).get("production_url", "")
    timeout_seconds = float(config.get("webhook", {}).get("timeout_seconds", 20))
    payload = build_segment_payload(session=session, segment_name=segment_name, limit=limit)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "url": target_url,
            "job_count": len(payload.get("body", [])),
            "payload": payload,
        }

    response = httpx.post(target_url, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    return {
        "ok": True,
        "dry_run": False,
        "url": target_url,
        "status_code": response.status_code,
        "job_count": len(payload.get("body", [])),
        "response_text": response.text[:1000],
    }
