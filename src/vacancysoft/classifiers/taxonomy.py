from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TaxonomyMatch:
    primary_taxonomy_key: str | None
    secondary_taxonomy_keys: list[str]
    confidence: float


# Each taxonomy key maps to a list of (pattern, weight) tuples.
# Phrases are checked before single words. Higher weight = stronger signal.
_TAXONOMY_RULES: dict[str, list[tuple[str, float]]] = {
    "risk": [
        ("credit risk", 1.0), ("market risk", 1.0), ("operational risk", 1.0),
        ("model risk", 1.0), ("liquidity risk", 1.0), ("enterprise risk", 1.0),
        ("insurance risk", 1.0), ("financial crime", 0.9), ("stress testing", 0.9),
        ("risk manager", 1.0), ("risk management", 1.0), ("risk officer", 1.0),
        ("risk analyst", 1.0), ("risk director", 1.0), ("head of risk", 1.0),
        ("chief risk", 1.0), ("operational resilience", 0.8), ("resilience", 0.6),
        ("treasury", 0.6), ("risk", 0.7), ("control", 0.4),
    ],
    "quant": [
        ("quantitative researcher", 1.0), ("quantitative analyst", 1.0),
        ("quantitative developer", 1.0), ("quantitative trader", 1.0),
        ("quant researcher", 1.0), ("quant analyst", 1.0),
        ("quant developer", 1.0), ("quant trader", 1.0),
        ("model validation", 0.9), ("strats", 0.9), ("pricing", 0.7),
        ("data scientist", 0.6), ("data science", 0.6),
        ("actuarial", 0.8), ("actuary", 0.8), ("derivatives", 0.6),
        ("structuring", 0.6), ("quantitative", 0.8), ("quant", 0.8),
    ],
    "compliance": [
        ("compliance officer", 1.0), ("compliance manager", 1.0),
        ("compliance analyst", 1.0), ("head of compliance", 1.0),
        ("chief compliance", 1.0), ("regulatory compliance", 1.0),
        ("aml analyst", 1.0), ("aml officer", 1.0), ("kyc analyst", 1.0),
        ("kyc officer", 1.0), ("financial crime", 0.9), ("surveillance", 0.8),
        ("governance", 0.6), ("compliance", 0.8), ("aml", 0.9), ("kyc", 0.9),
    ],
    "audit": [
        ("internal audit", 1.0), ("external audit", 1.0), ("it audit", 1.0),
        ("technology audit", 1.0), ("audit manager", 1.0), ("audit director", 1.0),
        ("head of audit", 1.0), ("assurance", 0.7), ("audit", 0.8),
    ],
    "cyber": [
        ("cyber security", 1.0), ("information security", 1.0),
        ("security engineer", 0.9), ("security architect", 0.9),
        ("penetration test", 0.9), ("threat detect", 0.9),
        ("red team", 0.9), ("offensive security", 0.9),
        ("cyber grc", 0.9), ("grc", 0.5), ("cyber", 0.8),
    ],
    "legal": [
        ("legal counsel", 1.0), ("general counsel", 1.0),
        ("solicitor", 0.9), ("paralegal", 0.8),
        ("contract", 0.5), ("legal", 0.8), ("counsel", 0.7),
    ],
    "front_office": [
        ("portfolio manager", 1.0), ("portfolio management", 1.0),
        ("sales trader", 0.9), ("market maker", 0.9), ("market making", 0.9),
        ("fixed income trading", 1.0), ("equities trading", 1.0),
        ("fx trading", 1.0), ("credit trading", 1.0), ("rates trading", 1.0),
        ("electronic trading", 0.9), ("commodities trading", 1.0),
        ("investment analyst", 0.7), ("investment manager", 0.7),
        ("trader", 0.8), ("trading", 0.7), ("sales", 0.4),
        ("portfolio", 0.6), ("investment", 0.5),
    ],
}

# Precompile patterns: longer phrases first so they match before single words
_COMPILED_RULES: dict[str, list[tuple[re.Pattern[str], float]]] = {}
for _key, _rules in _TAXONOMY_RULES.items():
    _sorted = sorted(_rules, key=lambda r: len(r[0]), reverse=True)
    _COMPILED_RULES[_key] = [
        (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), weight)
        for phrase, weight in _sorted
    ]


def classify_against_legacy_taxonomy(title: str | None) -> TaxonomyMatch:
    if not title:
        return TaxonomyMatch(primary_taxonomy_key=None, secondary_taxonomy_keys=[], confidence=0.0)

    best_key: str | None = None
    best_weight = 0.0
    secondary: list[str] = []

    for taxonomy_key, patterns in _COMPILED_RULES.items():
        key_weight = 0.0
        for pattern, weight in patterns:
            if pattern.search(title):
                key_weight = max(key_weight, weight)
                break  # take the first (longest) match per taxonomy key

        if key_weight > 0:
            if key_weight > best_weight:
                if best_key is not None:
                    secondary.append(best_key)
                best_key = taxonomy_key
                best_weight = key_weight
            else:
                secondary.append(taxonomy_key)

    confidence = round(best_weight * 0.90, 2) if best_key else 0.10
    return TaxonomyMatch(
        primary_taxonomy_key=best_key,
        secondary_taxonomy_keys=secondary,
        confidence=confidence,
    )
