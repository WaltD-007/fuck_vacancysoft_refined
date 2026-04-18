"""Campaign email generation orchestrator.

Loads an existing dossier and its associated job data, then calls
the OpenAI API with the campaign prompt to generate outreach emails.
"""

from __future__ import annotations

import logging
from typing import Any

import tomllib
from sqlalchemy.orm import Session

from vacancysoft.db.models import (
    CampaignOutput,
    EnrichedJob,
    IntelligenceDossier,
    RawJob,
    Source,
)
from vacancysoft.intelligence.client import call_chat
from vacancysoft.intelligence.prompts.resolver import resolve_campaign_prompt

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
) -> CampaignOutput:
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
        "candidate_profiles": dossier.candidate_profiles or [],
        "spec_risk": dossier.spec_risk or [],
    }

    messages = resolve_campaign_prompt(dossier.category_used, job_data, dossier_sections)
    config = _load_intel_config()

    result = await call_chat(
        model=config.get("campaign_model", "gpt-4o"),
        messages=messages,
        temperature=config.get("temperature", 0.4),
        max_tokens=config.get("max_tokens", 8000),
        timeout_seconds=config.get("timeout_seconds", 120),
        response_format={"type": "json_object"},
    )

    parsed = result["parsed"]

    campaign = CampaignOutput(
        dossier_id=dossier_id,
        model_used=result["model"],
        outreach_emails=parsed.get("emails"),
        raw_response=parsed,
        tokens_used=result["tokens_total"],
        latency_ms=result["latency_ms"],
    )
    session.add(campaign)
    session.commit()

    email_count = len(parsed.get("emails") or [])
    logger.info(
        "Campaign generated for dossier %s — %d emails, %d tokens, %dms",
        dossier_id, email_count, result["tokens_total"], result["latency_ms"],
    )
    return campaign
