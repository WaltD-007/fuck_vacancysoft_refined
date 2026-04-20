"""Campaign email generation orchestrator.

Loads an existing dossier and its associated job data, then calls
the OpenAI API with the campaign prompt to generate outreach emails.
"""

from __future__ import annotations

import logging
from typing import Any

import tomllib
from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import (
    CampaignOutput,
    EnrichedJob,
    IntelligenceDossier,
    RawJob,
    Source,
)
from vacancysoft.intelligence.prompts.resolver import resolve_campaign_prompt
from vacancysoft.intelligence.providers import LLMProvider, call_llm

logger = logging.getLogger(__name__)


def _load_intel_config() -> dict[str, Any]:
    try:
        with open("configs/app.toml", "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("intelligence", {})
    except Exception:
        return {}


async def generate_campaign(
    dossier_id: str,
    session: Session,
    force: bool = False,
) -> CampaignOutput:
    """Generate (or return cached) campaign emails for a dossier.

    By default this reads the most recent campaign for the dossier
    and returns it without calling the LLM if one exists with a
    populated outreach_emails payload. Pass force=True to bypass
    the cache.
    """
    if not force:
        existing = session.execute(
            select(CampaignOutput)
            .where(CampaignOutput.dossier_id == dossier_id)
            .order_by(CampaignOutput.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing and existing.outreach_emails:
            logger.info(
                "Reusing cached campaign %s for dossier %s — skipping LLM call",
                existing.id, dossier_id,
            )
            return existing

    dossier = session.get(IntelligenceDossier, dossier_id)
    if not dossier:
        raise ValueError(f"IntelligenceDossier {dossier_id} not found")

    enriched = session.get(EnrichedJob, dossier.enriched_job_id)
    if not enriched:
        raise ValueError(f"EnrichedJob {dossier.enriched_job_id} not found")

    raw = session.get(RawJob, enriched.raw_job_id)
    source = session.get(Source, raw.source_id) if raw else None

    job_data = {
        "title": enriched.title or "",
        "company": source.employer_name if source else "",
        "location": enriched.location_text or "",
    }

    dossier_sections = {
        "company_context": dossier.company_context or "",
        "core_problem": dossier.core_problem or "",
        "stated_vs_actual": dossier.stated_vs_actual or [],
        "candidate_profiles": dossier.candidate_profiles or [],
        "spec_risk": dossier.spec_risk or [],
        "lead_score_justification": dossier.lead_score_justification or "",
        "hiring_managers": dossier.hiring_managers or [],
    }

    config = _load_intel_config()
    # Campaign prompt template selector (2026-04-20): flip
    # campaign_template_version in configs/app.toml between "v2" (new
    # "tone controls content" shape, default) and "v1" (legacy "same
    # message, different voice" shape) without a code deploy. See
    # base_campaign.py docstring for the full rationale.
    campaign_template_version = str(
        config.get("campaign_template_version", "v2") or "v2"
    ).lower()
    messages = resolve_campaign_prompt(
        dossier.category_used,
        job_data,
        dossier_sections,
        template_version=campaign_template_version,
    )

    # Provider toggle: set use_deepseek_for_campaign=true in configs/app.toml
    # to route both the primary campaign call and its fallback through
    # DeepSeek. The fallback kicks in if the reasoner returns empty content
    # (either provider's reasoner can burn the full max_tokens budget on
    # internal reasoning), and uses a non-reasoning model in the same
    # provider family so it can't hit the same trap.
    use_deepseek_campaign = bool(config.get("use_deepseek_for_campaign", False))
    if use_deepseek_campaign:
        campaign_provider = LLMProvider.DEEPSEEK
        primary_model = config.get("campaign_model_deepseek", "deepseek-reasoner")
        fallback_model = config.get("campaign_fallback_model_deepseek", "deepseek-chat")
    else:
        campaign_provider = LLMProvider.OPENAI
        primary_model = config.get("campaign_model", "gpt-4o")
        fallback_model = config.get("campaign_fallback_model", "gpt-4o")

    result = await call_llm(
        provider=campaign_provider,
        model=primary_model,
        messages=messages,
        temperature=config.get("temperature", 0.4),
        max_tokens=config.get("max_tokens", 8000),
        timeout_seconds=config.get("timeout_seconds", 120),
        response_format={"type": "json_object"},
        reasoning_effort=config.get("campaign_reasoning_effort", "low"),
    )

    parsed = result["parsed"]
    emails = parsed.get("emails") if isinstance(parsed, dict) else None

    # Reasoning models can burn the entire max_completion_tokens budget on
    # internal reasoning and return empty visible content. Detect that and
    # fall back to a non-reasoning model that won't hit the same trap.
    if not emails and primary_model != fallback_model:
        logger.warning(
            "Campaign returned no emails on %s (tokens=%d completion=%d) — "
            "retrying with fallback %s",
            primary_model, result["tokens_total"], result["tokens_completion"],
            fallback_model,
        )
        result = await call_llm(
            provider=campaign_provider,
            model=fallback_model,
            messages=messages,
            temperature=config.get("temperature", 0.4),
            max_tokens=config.get("max_tokens", 8000),
            timeout_seconds=config.get("timeout_seconds", 120),
            response_format={"type": "json_object"},
        )
        parsed = result["parsed"]
        emails = parsed.get("emails") if isinstance(parsed, dict) else None

    if not emails:
        # Both models failed to produce a parseable email list — don't
        # persist a broken row that the worker would mark "ready".
        raise RuntimeError(
            f"Campaign generation produced no emails for dossier {dossier_id} "
            f"on {primary_model} (and fallback {fallback_model}). "
            f"Last response keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__}"
        )

    from vacancysoft.intelligence.pricing import compute_cost
    cost_usd = compute_cost(result["model"], result["tokens_prompt"], result["tokens_completion"])

    campaign = CampaignOutput(
        dossier_id=dossier_id,
        model_used=result["model"],
        outreach_emails=emails,
        raw_response=parsed,
        tokens_used=result["tokens_total"],
        tokens_prompt=result["tokens_prompt"],
        tokens_completion=result["tokens_completion"],
        cost_usd=cost_usd,
        latency_ms=result["latency_ms"],
    )
    session.add(campaign)
    session.commit()

    logger.info(
        "Campaign generated for dossier %s — %d emails on %s, %d tokens, %dms, $%.4f",
        dossier_id, len(emails), result["model"], result["tokens_total"],
        result["latency_ms"], cost_usd,
    )
    return campaign
