"""Assembles final prompts by merging base templates with category-specific blocks."""

from __future__ import annotations

import logging
from typing import Any

from vacancysoft.intelligence.prompts.base_campaign import CAMPAIGN_SYSTEM, CAMPAIGN_TEMPLATE
from vacancysoft.intelligence.prompts.base_dossier import DOSSIER_SYSTEM, DOSSIER_TEMPLATE
from vacancysoft.intelligence.prompts.category_blocks import CATEGORY_BLOCKS, DEFAULT_CATEGORY

logger = logging.getLogger(__name__)


def _get_blocks(category: str) -> dict[str, str]:
    blocks = CATEGORY_BLOCKS.get(category)
    if not blocks:
        logger.warning("No prompt blocks for category %r, falling back to %r", category, DEFAULT_CATEGORY)
        blocks = CATEGORY_BLOCKS[DEFAULT_CATEGORY]
    return blocks


def resolve_dossier_prompt(
    category: str,
    job_data: dict[str, Any],
) -> list[dict[str, str]]:
    blocks = _get_blocks(category)
    user_content = DOSSIER_TEMPLATE.format(
        title=job_data.get("title", ""),
        company=job_data.get("company", ""),
        location=job_data.get("location", ""),
        date_posted=job_data.get("date_posted", ""),
        description=job_data.get("description", ""),
        research_scope=blocks["research_scope"],
        market_context_guidance=blocks["market_context_guidance"],
        search_boolean_guidance=blocks["search_boolean_guidance"],
        hm_function_guidance=blocks["hm_function_guidance"],
        hm_search_queries=blocks["hm_search_queries"],
    )
    return [
        {"role": "system", "content": DOSSIER_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def resolve_campaign_prompt(
    category: str,
    job_data: dict[str, Any],
    dossier_sections: dict[str, Any],
) -> list[dict[str, str]]:
    blocks = _get_blocks(category)

    profiles = dossier_sections.get("candidate_profiles") or []
    profile_summary = ""
    for p in profiles[:2]:
        if isinstance(p, dict):
            profile_summary += f"{p.get('label', '')}: {p.get('background', '')} — {p.get('fit_reason', '')}\n"

    risks = dossier_sections.get("spec_risk") or []
    risk_summary = ""
    for r in risks[:4]:
        if isinstance(r, dict):
            risk_summary += f"[{r.get('severity', '')}] {r.get('risk', '')}\n"

    user_content = CAMPAIGN_TEMPLATE.format(
        title=job_data.get("title", ""),
        company=job_data.get("company", ""),
        location=job_data.get("location", ""),
        company_context=dossier_sections.get("company_context", ""),
        core_problem=dossier_sections.get("core_problem", ""),
        candidate_profile_summary=profile_summary.strip() or "Not available",
        spec_risk_summary=risk_summary.strip() or "Not available",
        outreach_angle=blocks["outreach_angle"],
    )
    return [
        {"role": "system", "content": CAMPAIGN_SYSTEM},
        {"role": "user", "content": user_content},
    ]
