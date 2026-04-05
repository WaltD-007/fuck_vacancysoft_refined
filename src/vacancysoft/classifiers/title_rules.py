from __future__ import annotations


def title_relevance(title: str | None) -> float:
    if not title:
        return 0.0
    title_l = title.lower()
    keywords = ["risk", "quant", "compliance", "audit", "cyber", "legal", "trader"]
    return 0.9 if any(word in title_l for word in keywords) else 0.2
