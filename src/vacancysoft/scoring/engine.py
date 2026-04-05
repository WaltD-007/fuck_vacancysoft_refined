from __future__ import annotations


def compute_export_score(
    title_relevance: float,
    location_confidence: float,
    freshness_confidence: float,
    source_reliability: float,
    completeness: float,
    classification_confidence: float,
) -> float:
    return round(
        0.30 * title_relevance
        + 0.15 * location_confidence
        + 0.15 * freshness_confidence
        + 0.10 * source_reliability
        + 0.10 * completeness
        + 0.20 * classification_confidence,
        4,
    )
