from __future__ import annotations

from vacancysoft.classifiers.employment_type import classify_employment_type
from vacancysoft.classifiers.taxonomy import classify_against_legacy_taxonomy
from vacancysoft.classifiers.title_rules import title_relevance
from vacancysoft.schemas.classification import ClassificationPayload


def build_classification_payload(enriched_job_id: str, title: str | None) -> ClassificationPayload:
    taxonomy = classify_against_legacy_taxonomy(title)
    relevance = title_relevance(title)
    employment_type = classify_employment_type(title)
    decision = "accepted" if relevance >= 0.75 else "review" if relevance >= 0.45 else "rejected"
    return ClassificationPayload(
        enriched_job_id=enriched_job_id,
        taxonomy_version="legacy_v1",
        primary_taxonomy_key=taxonomy.primary_taxonomy_key,
        secondary_taxonomy_keys=taxonomy.secondary_taxonomy_keys,
        sub_specialism=taxonomy.sub_specialism,
        sub_specialism_confidence=taxonomy.sub_specialism_confidence,
        employment_type=employment_type,
        title_relevance_score=relevance,
        classification_confidence=taxonomy.confidence,
        decision=decision,
        reasons={
            "title": title,
            "taxonomy_match": taxonomy.primary_taxonomy_key,
            "employment_type": employment_type,
        },
    )
