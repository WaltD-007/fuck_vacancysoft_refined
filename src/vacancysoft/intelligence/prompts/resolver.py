"""Assembles final prompts by merging base templates with category-specific blocks."""

from __future__ import annotations

import logging
from typing import Any

from vacancysoft.intelligence.prompts.base_campaign import (
    CAMPAIGN_SYSTEM,
    CAMPAIGN_TEMPLATE_V1,
    CAMPAIGN_TEMPLATE_V2,
)
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
    template_version: str = "v2",
) -> list[dict[str, str]]:
    """Assemble the campaign prompt.

    Feeds the full analytical surface of the dossier into the campaign
    prompt — not just company_context + core_problem like the earlier
    version did. The campaign model now sees:

    - company_context (unchanged)
    - core_problem (unchanged)
    - stated_vs_actual rows as "JD asks X / business needs Y" pairs
    - spec_risk items WITH explanations (was: bare risk header only)
    - candidate_profiles including outcomes (was: only background + fit)
    - lead_score_justification (was: not passed at all)
    - the highest-confidence hiring manager's name/title if available
    - outreach_angle (unchanged, from category block)

    Missing context was the dominant quality limit on the previous
    campaign output — the model was writing about a generic role at a
    generic firm because it had a 400-token summary of a 5,000-token
    dossier to work from. This doubles the input tokens but hands
    everything the model needs to write company-specific outreach.
    """
    blocks = _get_blocks(category)

    # Candidate profiles: include outcomes now; still cap at 2
    profiles = dossier_sections.get("candidate_profiles") or []
    profile_lines: list[str] = []
    for p in profiles[:2]:
        if not isinstance(p, dict):
            continue
        label = p.get("label", "")
        background = p.get("background", "")
        fit = p.get("fit_reason", "")
        outcomes = p.get("outcomes", "")
        profile_lines.append(
            f"{label}: {background} | fit: {fit}"
            + (f" | outcomes: {outcomes}" if outcomes else "")
        )
    profile_summary = "\n".join(profile_lines) or "Not available"

    # Spec risk: include explanation now, cap at 3 (was 4 headers only)
    risks = dossier_sections.get("spec_risk") or []
    risk_lines: list[str] = []
    for r in risks[:3]:
        if not isinstance(r, dict):
            continue
        severity = r.get("severity", "")
        risk = r.get("risk", "")
        explanation = r.get("explanation", "")
        risk_lines.append(
            f"[{severity}] {risk}"
            + (f" — {explanation}" if explanation else "")
        )
    risk_summary = "\n".join(risk_lines) or "Not available"

    # Stated vs actual: new in this version
    sva = dossier_sections.get("stated_vs_actual") or []
    sva_lines: list[str] = []
    for row in sva[:3]:
        if not isinstance(row, dict):
            continue
        asks = row.get("jd_asks_for", "")
        needs = row.get("business_likely_needs", "")
        if asks or needs:
            sva_lines.append(f"JD asks: {asks} | Business likely needs: {needs}")
    stated_vs_actual_summary = "\n".join(sva_lines) or "Not available"

    # Lead-score justification: new in this version
    lsj = (dossier_sections.get("lead_score_justification") or "").strip()
    lead_score_context = lsj or "Not available"

    # Top hiring manager: new in this version. Prefer highest-confidence,
    # fall back to first listed if no confidences ranked equally.
    hms = dossier_sections.get("hiring_managers") or []
    _CONF_RANK = {"high": 3, "medium": 2, "low": 1}
    top_hm = None
    best_rank = -1
    for hm in hms:
        if not isinstance(hm, dict):
            continue
        rank = _CONF_RANK.get((hm.get("confidence") or "").lower(), 0)
        if rank > best_rank:
            best_rank = rank
            top_hm = hm
    if top_hm:
        hm_name = top_hm.get("name", "").strip()
        hm_title = top_hm.get("title", "").strip()
        hiring_manager_line = (
            f"{hm_name} — {hm_title}" if hm_name and hm_title
            else hm_name or hm_title or "Not identified"
        )
    else:
        hiring_manager_line = "Not identified"

    # Template selection: v2 (default, 2026-04-20+) reshapes the prompt so
    # tone determines content-source, not just register. v1 is the legacy
    # "same message, different voice" shape and is kept behind the flag
    # for hot-swap rollback. v2 ignores {outreach_angle} — .format() is
    # permissive about unused kwargs so we pass it either way.
    tv = (template_version or "v2").lower()
    template = CAMPAIGN_TEMPLATE_V1 if tv == "v1" else CAMPAIGN_TEMPLATE_V2

    user_content = template.format(
        title=job_data.get("title", ""),
        company=job_data.get("company", ""),
        location=job_data.get("location", ""),
        company_context=dossier_sections.get("company_context", "") or "Not available",
        core_problem=dossier_sections.get("core_problem", "") or "Not available",
        stated_vs_actual_summary=stated_vs_actual_summary,
        spec_risk_summary=risk_summary,
        candidate_profile_summary=profile_summary,
        lead_score_context=lead_score_context,
        hiring_manager_line=hiring_manager_line,
        outreach_angle=blocks["outreach_angle"],
    )
    return [
        {"role": "system", "content": CAMPAIGN_SYSTEM},
        {"role": "user", "content": user_content},
    ]
