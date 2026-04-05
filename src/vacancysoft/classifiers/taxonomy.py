from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TaxonomyMatch:
    primary_taxonomy_key: str | None
    secondary_taxonomy_keys: list[str]
    confidence: float


def classify_against_legacy_taxonomy(title: str | None) -> TaxonomyMatch:
    if not title:
        return TaxonomyMatch(primary_taxonomy_key=None, secondary_taxonomy_keys=[], confidence=0.0)

    value = title.lower()
    mapping = {
        "risk": ["risk", "control"],
        "quant": ["quant", "quantitative", "model"],
        "compliance": ["compliance", "aml", "kyc", "surveillance"],
        "audit": ["audit", "assurance"],
        "cyber": ["cyber", "security"],
        "legal": ["legal", "counsel", "contract"],
        "front_office": ["trader", "sales", "portfolio", "investment"],
    }
    matches = [key for key, terms in mapping.items() if any(term in value for term in terms)]
    primary = matches[0] if matches else None
    confidence = 0.85 if primary else 0.15
    return TaxonomyMatch(primary_taxonomy_key=primary, secondary_taxonomy_keys=matches[1:], confidence=confidence)
