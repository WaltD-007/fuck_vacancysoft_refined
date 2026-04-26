"""LLM-assisted sector classification for unknown employers.

Used by ``prospero db classify-unknowns`` to chip away at the
``sector='unknown'`` bucket left over after the deterministic
classifier (``sector_classifier.detect_sector``) returns 'unknown'.

The function takes an employer name plus a few sample job titles
and asks the LLM (OpenAI gpt-5-nano by default — cheap, fast) to
return a single sector key from the allowed taxonomy.

Output contract is JSON:
    {"sector": "<one of allowed_sectors>", "confidence": 0.0-1.0, "reasoning": "..."}

Cost (gpt-5-nano @ ~$0.05/1M input, $0.40/1M output):
~50 input tokens + ~30 output tokens per firm = ~$0.0001/firm.
Classifying ~800 unknowns costs ~$0.08.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from vacancysoft.intelligence.client import call_chat
from vacancysoft.source_registry.sector_classifier import allowed_sectors

logger = logging.getLogger(__name__)


# Default model — lean cheap. Override via env or CLI flag if needed.
_DEFAULT_MODEL = "gpt-5-nano"


def _build_prompt(
    employer: str,
    sample_titles: list[str],
    sample_locations: list[str],
) -> tuple[str, str]:
    """Return (system, user) messages for the classification call."""
    sectors = sorted(allowed_sectors())
    sectors_str = "\n".join(f"  - {s}" for s in sectors if s != "unknown")
    titles_block = "\n".join(f"  - {t}" for t in sample_titles[:5]) if sample_titles else "  (none)"
    locations_block = (
        ", ".join(loc for loc in sample_locations[:5] if loc) if sample_locations else "(none)"
    )
    system = (
        "You classify financial-services employers into a fixed sector taxonomy. "
        "Return STRICT JSON only — no commentary, no markdown."
    )
    user = (
        f"Employer name: {employer}\n"
        f"Sample job titles for this employer:\n{titles_block}\n"
        f"Sample locations: {locations_block}\n\n"
        "Pick the SINGLE best-fit sector from this list:\n"
        f"{sectors_str}\n\n"
        'Return JSON: {"sector": "<key>", "confidence": <0.0-1.0>, "reasoning": "<short>"}\n\n'
        "Rules:\n"
        "  - sector MUST be one of the keys above (lowercase, snake_case).\n"
        "  - confidence reflects how certain you are; <0.5 = guess.\n"
        "  - reasoning is one sentence max.\n"
        "  - If the firm is genuinely outside financial services, return "
        '"sector": "unknown" with low confidence — do NOT force-fit.\n'
        "  - 'aggregator' is reserved for job-board adapters (Adzuna, Reed, etc.); "
        "do NOT use it for any real employer."
    )
    return system, user


async def classify_employer(
    employer: str,
    sample_titles: list[str] | None = None,
    sample_locations: list[str] | None = None,
    *,
    model: str = _DEFAULT_MODEL,
) -> dict[str, Any]:
    """Classify a single employer via LLM.

    Returns: {"sector": str, "confidence": float, "reasoning": str,
              "tokens_total": int, "raw": dict}.
    On any failure (timeout, parse error, invalid sector) returns
    sector='unknown' with confidence=0.0 so the caller can skip the
    row safely.
    """
    sample_titles = sample_titles or []
    sample_locations = sample_locations or []
    system, user = _build_prompt(employer, sample_titles, sample_locations)
    allowed = allowed_sectors()

    try:
        result = await call_chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,  # deterministic — same input → same answer
            max_tokens=200,
            timeout_seconds=30,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("LLM classify failed for %r: %s", employer, exc)
        return {
            "sector": "unknown",
            "confidence": 0.0,
            "reasoning": f"LLM call failed: {exc}",
            "tokens_total": 0,
            "raw": None,
        }

    parsed = result.get("parsed") or {}
    if not isinstance(parsed, dict):
        return {
            "sector": "unknown",
            "confidence": 0.0,
            "reasoning": "Non-dict LLM response",
            "tokens_total": result.get("tokens_total", 0),
            "raw": result,
        }

    sector = str(parsed.get("sector", "")).strip().lower()
    if sector not in allowed:
        # Defensive: model returned a sector outside the enum. Force unknown.
        return {
            "sector": "unknown",
            "confidence": 0.0,
            "reasoning": f"LLM returned out-of-enum sector: {sector!r}",
            "tokens_total": result.get("tokens_total", 0),
            "raw": result,
        }
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "sector": sector,
        "confidence": max(0.0, min(1.0, confidence)),
        "reasoning": str(parsed.get("reasoning", "") or "")[:300],
        "tokens_total": result.get("tokens_total", 0),
        "raw": result,
    }
