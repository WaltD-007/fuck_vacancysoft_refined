from __future__ import annotations

import re

# Phrases scored higher — these are unambiguously relevant job titles
HIGH_RELEVANCE_PHRASES: list[str] = [
    "risk manager", "risk officer", "risk analyst", "risk director", "head of risk",
    "chief risk", "credit risk", "market risk", "operational risk", "model risk",
    "liquidity risk", "enterprise risk", "financial crime",
    "quantitative researcher", "quantitative analyst", "quantitative developer",
    "quantitative trader", "quant researcher", "quant analyst", "quant developer",
    "quant trader", "strats", "pricing analyst",
    "compliance officer", "compliance manager", "compliance analyst", "head of compliance",
    "chief compliance", "aml analyst", "aml officer", "kyc analyst", "kyc officer",
    "internal audit", "audit manager", "audit director", "head of audit", "it audit",
    "cyber security", "information security", "security engineer", "security architect",
    "penetration test", "threat detect", "red team",
    "legal counsel", "general counsel", "solicitor", "paralegal",
    "portfolio manager", "trader", "sales trader", "market maker",
    "actuarial", "actuary", "stress testing", "model validation",
]

# Single keywords — relevant but could appear in non-target roles
MEDIUM_RELEVANCE_WORDS: list[str] = [
    "risk", "quant", "quantitative", "compliance", "audit", "cyber",
    "legal", "trader", "trading", "derivatives", "structuring",
    "surveillance", "governance", "resilience", "treasury",
]

# Word-boundary pattern cache
_PHRASE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in HIGH_RELEVANCE_PHRASES
]
_WORD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE) for word in MEDIUM_RELEVANCE_WORDS
]


def title_relevance(title: str | None) -> float:
    if not title:
        return 0.0

    # Check high-relevance phrases first
    for pattern in _PHRASE_PATTERNS:
        if pattern.search(title):
            return 0.95

    # Then single keywords with word boundaries
    for pattern in _WORD_PATTERNS:
        if pattern.search(title):
            return 0.80

    return 0.15
