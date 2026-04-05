from __future__ import annotations

from vacancysoft.schemas.classification import ClassificationPayload


def build_taxonomy_export_row(payload: ClassificationPayload, title: str | None, employer: str | None) -> dict:
    return {
        "employer": employer,
        "title": title,
        "primary_taxonomy_key": payload.primary_taxonomy_key,
        "secondary_taxonomy_keys": payload.secondary_taxonomy_keys,
        "taxonomy_version": payload.taxonomy_version,
        "decision": payload.decision,
        "classification_confidence": payload.classification_confidence,
    }
