"""LLM pricing tables for cost calculation.

OpenAI rates verified against the developer-platform pricing
dashboard 2026-04-27 (Standard tier, short-context). The table
does not model the long-context tier (input/output ~2× short),
the cached-input discount, the Batch / Flex / Priority lanes, or
the +10% regional-residency uplift — these change pricing by
~0.1×-2×, so per-call cost computed here is a list-price estimate
rather than a billed-amount oracle. Update when the dashboard
moves or when your contracted tier rates differ.

Covers both OpenAI and DeepSeek models. Model-name prefix matching
(see ``_resolve_rates``) disambiguates families so entries can be
listed in one table without collisions. Longer keys win the prefix
match so e.g. "gpt-5.4-mini-2026-04-XX" picks "gpt-5.4-mini" over
"gpt-5.4".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (input_per_1m_usd, output_per_1m_usd) — Standard tier, short context.
PRICING: dict[str, tuple[float, float]] = {
    # GPT-5 family — flagship pricing
    "gpt-5.5-pro":  (30.00, 180.00),
    "gpt-5.5":      ( 5.00,  30.00),  # campaign default since 2026-04-27 (V3 + 5.5)
    "gpt-5.4-pro":  (30.00, 180.00),
    "gpt-5.4-mini": ( 0.75,   4.50),
    "gpt-5.4-nano": ( 0.20,   1.25),
    "gpt-5.4":      ( 2.50,  15.00),  # campaign default until 2026-04-27 (V2 + 5.4)
    "gpt-5.2":      ( 1.25,  10.00),  # dossier default
    "gpt-5":        ( 1.25,  10.00),
    "gpt-5-mini":   ( 0.25,   2.00),
    # GPT-4o family
    "gpt-4o":      (2.50, 10.00),     # campaign fallback when reasoning model returns empty
    "gpt-4o-mini": (0.15,  0.60),     # advert-extraction model
    # GPT-4 / 4-turbo (legacy)
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4":       (30.00, 60.00),
    # Reasoning models (o-series, legacy)
    "o1":          (15.00, 60.00),
    "o1-mini":     ( 3.00, 12.00),
    "o3":          (15.00, 60.00),
    "o3-mini":     ( 3.00, 12.00),
    "o4-mini":     ( 3.00, 12.00),
    # DeepSeek family — standard-rate prices (cache-miss input).
    # DeepSeek offers ~50% off during their designated off-peak window;
    # compute_cost() does not currently model off-peak discounts.
    # Verify at https://api-docs.deepseek.com/quick_start/pricing
    "deepseek-reasoner": (0.55, 2.19),  # R1 — reasoning model, used when use_deepseek_for_* is on
    "deepseek-chat":     (0.27, 1.10),  # V3 — general chat, used as the DeepSeek-side campaign fallback
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
    """Return estimated USD cost for a single LLM call.

    Works for both OpenAI and DeepSeek model IDs — ``_resolve_rates``
    looks them up in the same shared ``PRICING`` table.

    Cost is rounded to 6 decimal places (i.e. micro-dollars) so even
    cheap calls produce a non-zero figure that aggregates correctly.
    """
    in_rate, out_rate = _resolve_rates(model)
    cost = (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000
    return round(cost, 6)
