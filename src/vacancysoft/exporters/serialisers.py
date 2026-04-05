from __future__ import annotations

from datetime import datetime
from typing import Any

EXPORT_COLUMNS = [
    "enriched_job_id",
    "title",
    "location_text",
    "location_city",
    "location_country",
    "posted_at",
    "primary_taxonomy_key",
    "secondary_taxonomy_keys",
    "taxonomy_version",
    "classifier_version",
    "classification_decision",
    "export_decision",
    "export_eligibility_score",
    "scoring_version",
    "employer_name",
    "source_key",
    "discovered_url",
    "apply_url",
]

N8N_FIELD_MAP = {
    "enriched_job_id": "id",
    "title": "job_title",
    "location_text": "location",
    "location_city": "location_city",
    "location_country": "location_country",
    "posted_at": "posted_at",
    "primary_taxonomy_key": "taxonomy",
    "secondary_taxonomy_keys": "secondary_taxonomies",
    "taxonomy_version": "taxonomy_version",
    "classifier_version": "classifier_version",
    "classification_decision": "classification_decision",
    "export_decision": "export_decision",
    "export_eligibility_score": "export_score",
    "scoring_version": "scoring_version",
    "employer_name": "company",
    "source_key": "source_key",
    "discovered_url": "job_url",
    "apply_url": "apply_url",
}


def row_to_dict(row: Any) -> dict[str, Any]:
    mapping = row._mapping if hasattr(row, "_mapping") else dict(row)
    result: dict[str, Any] = {}
    for column in EXPORT_COLUMNS:
        value = mapping.get(column)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column] = value
    return result


def row_to_n8n_job(row: Any) -> dict[str, Any]:
    base = row_to_dict(row)
    payload: dict[str, Any] = {}
    for source_key, target_key in N8N_FIELD_MAP.items():
        payload[target_key] = base.get(source_key)
    return payload


def build_jobs_envelope(rows: list[Any], profile_name: str) -> dict[str, Any]:
    jobs = [row_to_n8n_job(row) for row in rows]
    return {
        "profile": profile_name,
        "job_count": len(jobs),
        "jobs": jobs,
    }
