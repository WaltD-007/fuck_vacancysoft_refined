from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from vacancysoft.exporters.profiles import resolve_profile_query, resolve_segment_query
from vacancysoft.exporters.serialisers import build_legacy_webhook_payload
from vacancysoft.exporters.views import fetch_rows, load_exporter_config


def build_profile_payload(session: Session, profile_name: str, limit: int = 100) -> dict:
    config = load_exporter_config()
    stmt = resolve_profile_query(profile_name, config)
    rows = fetch_rows(session, stmt, limit=limit)
    return build_legacy_webhook_payload(rows)


def build_segment_payload(session: Session, segment_name: str, limit: int = 100) -> dict:
    config = load_exporter_config()
    stmt = resolve_segment_query(segment_name, config)
    rows = fetch_rows(session, stmt, limit=limit)
    return build_legacy_webhook_payload(rows)


def export_profile_to_json(session: Session, profile_name: str, output_path: str | Path, limit: int = 100) -> Path:
    payload = build_profile_payload(session=session, profile_name=profile_name, limit=limit)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output


def export_segment_to_json(session: Session, segment_name: str, output_path: str | Path, limit: int = 100) -> Path:
    payload = build_segment_payload(session=session, segment_name=segment_name, limit=limit)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output
