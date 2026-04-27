"""Assembles final prompts by merging base templates with category-specific blocks."""

from __future__ import annotations

import logging
from typing import Any

from vacancysoft.intelligence.prompts.base_campaign import (
    CAMPAIGN_SYSTEM,
    CAMPAIGN_TEMPLATE_V1,
    CAMPAIGN_TEMPLATE_V2,
    CAMPAIGN_TEMPLATE_V3,
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


_CAMPAIGN_TONES: tuple[str, ...] = (
    "formal", "informal", "consultative", "direct", "candidate_spec", "technical",
)


def _render_voice_layer(user_context: dict[str, Any] | None) -> str:
    """Build the `{voice_layer}` section the v2 template slots in.

    Returns an empty string when:
      * user_context is None (worker pre-gen path) — byte-identical
        to pre-voice-layer output so regression risk is zero.
      * user_context has no authored tone prompts AND no voice
        samples — a cold-start user with only an id/email.

    Otherwise returns a block starting with a leading newline so the
    template rendering lands cleanly between the preceding
    "Do not invent dossier details." line and the "# Output schema"
    heading.
    """
    if not user_context:
        return ""

    tone_prompts: dict[str, str] = user_context.get("tone_prompts") or {}
    voice_samples: dict[int, list[dict]] = user_context.get("voice_samples_by_step") or {}
    display_name = (user_context.get("display_name") or "the operator").strip()

    has_authored = any((tone_prompts.get(t) or "").strip() for t in _CAMPAIGN_TONES)
    has_samples = any(voice_samples.get(seq) for seq in range(1, 6))
    if not has_authored and not has_samples:
        return ""

    parts: list[str] = ["", f"# Voice layer for {display_name}", ""]

    if has_authored:
        parts.extend([
            "## Authored voice guidance (per-tone overrides from the operator)",
            "",
            "The guidance below was authored by this operator. Where a tone has guidance, it TAKES PRECEDENCE over the default tone->source voice notes for voice/phrasing decisions. It does NOT override the structural rules (source mapping, five-sequence arc, closed-list CTAs, anti-stage-leak guards, spoken-English guards).",
            "",
        ])
        for tone in _CAMPAIGN_TONES:
            text = (tone_prompts.get(tone) or "").strip()
            if text:
                # Escape braces so the overall template.format() call
                # doesn't misinterpret operator-written { or } (tech
                # stacks, code snippets) as placeholders.
                safe = text.replace("{", "{{").replace("}", "}}")
                parts.append(f"- **{tone}**: {safe}")
            else:
                parts.append(f"- **{tone}**: (no override — use default)")
        parts.append("")

    if has_samples:
        parts.extend([
            f"## How {display_name} actually writes (last 5 sent messages per sequence)",
            "",
            "The emails below were sent by this operator. Learn the voice — sentence length, opener patterns, closer patterns, word choice, rhythm. Do NOT copy subjects or phrasings verbatim. Do NOT invent signed-off-by text from these; they are voice reference only. Do NOT quote them as if they were part of this conversation.",
            "",
        ])
        for seq in range(1, 6):
            rows = voice_samples.get(seq) or []
            if not rows:
                continue
            parts.append(f"### Sequence {seq} samples (most recent first)")
            parts.append("")
            for idx, row in enumerate(rows, start=1):
                # Same brace-escape reasoning as above — operator-
                # written bodies may contain curly braces.
                subj = (row.get("subject") or "").replace("{", "{{").replace("}", "}}").strip()
                body = (row.get("body") or "").replace("{", "{{").replace("}", "}}").strip()
                tone = (row.get("tone") or "").strip() or "unknown"
                parts.append(f"{idx}. [tone: {tone}] Subject: \"{subj}\"")
                parts.append(f"   Body: \"{body}\"")
                parts.append("")

    return "\n".join(parts)


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
    template_version: str = "v3",
    user_context: dict[str, Any] | None = None,
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
    - description (raw JD body, capped at 6,000 chars, v2-only) — lets
      any tone ground its language in the advert's actual phrases /
      product names / reg references. Added 2026-04-21 after operator
      review showed the campaign quality ceiling was set by the
      dossier's JD-fidelity.
    - user_context (new 2026-04-21) — per-user voice layer. When
      populated, renders `# Voice layer for <name>` between the
      global rules and the output schema. Contains authored per-tone
      overrides and/or the last 5 actually-sent messages per
      sequence as few-shot examples. When None (worker pre-gen path)
      the voice_layer slot renders as empty string and output is
      byte-identical to pre-voice-layer behaviour.
    - outreach_angle (unchanged, from category block, v1-only)

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

    # Spec risk: include explanation now, cap at 4 (dossier section 4 —
    # base_dossier.py allows up to 4 items; 2026-04-21 bump from 3 → 4
    # so the campaign prompt sees everything the dossier produced).
    risks = dossier_sections.get("spec_risk") or []
    risk_lines: list[str] = []
    for r in risks[:4]:
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

    # Stated vs actual: new in this version. Cap at 4 (dossier section
    # 3 — base_dossier.py allows up to 4 rows; 2026-04-21 bump from 3 → 4).
    sva = dossier_sections.get("stated_vs_actual") or []
    sva_lines: list[str] = []
    for row in sva[:4]:
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

    # Raw JD body — v2-only appendix. Capped at _JD_MAX_CHARS so a
    # 20K-char Workday advert doesn't blow the input budget; the tail
    # is dropped after the cap (and marked in-line so the model knows).
    # Curly braces in the advert are escaped because the whole blob
    # then goes through str.format() below; JDs occasionally contain
    # code samples or template-like syntax that would otherwise raise
    # IndexError / KeyError inside .format().
    _JD_MAX_CHARS = 6000
    description_raw = (job_data.get("description") or "").strip()
    if len(description_raw) > _JD_MAX_CHARS:
        description_raw = (
            description_raw[:_JD_MAX_CHARS].rstrip()
            + "\n\n[… truncated — full JD is longer than 6,000 characters]"
        )
    description_safe = description_raw.replace("{", "{{").replace("}", "}}") or "Not available"

    # Template selection: v3 (default, 2026-04-27+, GPT-5.5) is a
    # persona-led aggressive rule-cut on V2; v2 (2026-04-20+) reshapes
    # the prompt so tone determines content-source, not just register;
    # v1 is the legacy "same message, different voice" shape. All three
    # are kept behind the config flag so a hot-swap revert is one line
    # of TOML and a restart. .format() is permissive about unused kwargs,
    # so all four format keys ({outreach_angle}, {description},
    # {voice_layer}, plus V3's {recruiter_specialism})
    # are passed regardless of which template is selected; templates
    # silently ignore the placeholders they don't reference.
    tv = (template_version or "v3").lower()
    if tv == "v1":
        template = CAMPAIGN_TEMPLATE_V1
    elif tv == "v2":
        template = CAMPAIGN_TEMPLATE_V2
    else:
        template = CAMPAIGN_TEMPLATE_V3

    # Voice-layer block. Empty string when user_context is None OR when
    # the user has no authored overrides and no voice samples yet —
    # either way V2 / V3 render exactly as they did pre-voice-layer.
    # V1 ignores the kwarg entirely.
    voice_layer = _render_voice_layer(user_context)

    # V3 persona placeholder. V1 / V2 ignore it (str.format is
    # permissive about unused kwargs).
    #
    # recruiter_specialism: short noun phrase that anchors the persona
    # in the right domain. New per-category key in CATEGORY_BLOCKS;
    # falls back to a generic phrase if a future category is added
    # without one. The operator's name is deliberately NOT injected
    # into the persona block — the voice layer carries personal voice
    # when populated, and the persona block stays operator-agnostic so
    # worker pre-gen and operator regenerations render identically.
    recruiter_specialism = blocks.get("recruiter_specialism", "recruitment specialist")

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
        description=description_safe,
        voice_layer=voice_layer,
        outreach_angle=blocks["outreach_angle"],
        recruiter_specialism=recruiter_specialism,
    )
    return [
        {"role": "system", "content": CAMPAIGN_SYSTEM},
        {"role": "user", "content": user_content},
    ]
