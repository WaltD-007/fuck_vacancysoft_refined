"""OpenAI pricing tables for cost calculation.

Public list pricing as of 2026-Q2. Update when OpenAI changes prices or
when your contracted tier rates differ. Costs computed here are estimates
— actual billed amounts can differ if you're on a custom enterprise tier.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (input_per_1m_usd, output_per_1m_usd)
PRICING: dict[str, tuple[float, float]] = {
    # GPT-5 family
    "gpt-5.2":     (1.25, 10.00),
    "gpt-5":       (1.25, 10.00),
    "gpt-5-mini":  (0.25,  2.00),
    # GPT-4o family
    "gpt-4o":      (2.50, 10.00),
    "gpt-4o-mini": (0.15,  0.60),
    # GPT-4 / 4-turbo
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4":       (30.00, 60.00),
    # Reasoning models (o-series)
    "o1":          (15.00, 60.00),
    "o1-mini":     ( 3.00, 12.00),
    "o3":          (15.00, 60.00),
    "o3-mini":     ( 3.00, 12.00),
    "o4-mini":     ( 3.00, 12.00),
}

# Used when no PRICING key matches. Errs on the conservative (high) side
# so cost is overestimated rather than under-reported for unknown models.
DEFAULT_PRICE: tuple[float, float] = (5.00, 15.00)


def _resolve_rates(model: str) -> tuple[float, float]:
    """Find the best-matching price entry for `model`.

    Tries an exact match first, then prefix match (longest prefix wins
    so "gpt-5-mini-2026-04-19" picks "gpt-5-mini" over "gpt-5"). Falls
    back to DEFAULT_PRICE if nothing matches.
    """
    if not model:
        return DEFAULT_PRICE
    if model in PRICING:
        return PRICING[model]
    for known in sorted(PRICING.keys(), key=len, reverse=True):
        if model.startswith(known):
            return PRICING[known]
    logger.warning("No pricing entry for model %r — using DEFAULT_PRICE %s", model, DEFAULT_PRICE)
    return DEFAULT_PRICE


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return estimated USD cost for a single OpenAI call.

    Cost is rounded to 6 decimal places (i.e. micro-dollars) so even
    cheap calls produce a non-zero figure that aggregates correctly.
    """
    in_rate, out_rate = _resolve_rates(model)
    cost = (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000
    return round(cost, 6)
