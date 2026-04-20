"""Dossier generation orchestrator.

Loads an enriched job and its classification, resolves the correct
category-specific prompt, calls the OpenAI API, and stores the result.

The hiring manager search runs as a separate focused API call with web
search enabled, to avoid the model taking shortcuts inside the larger
dossier prompt.
"""

from __future__ import annotations

import logging
import tomllib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import (
    ClassificationResult,
    EnrichedJob,
    IntelligenceDossier,
    RawJob,
    Source,
)
from vacancysoft.intelligence.prompts.category_blocks import (
    CATEGORY_BLOCKS,
    DEFAULT_CATEGORY,
    render_hm_search_template_v2,
)
from vacancysoft.intelligence.prompts.resolver import resolve_dossier_prompt
from vacancysoft.intelligence.providers import LLMProvider, call_llm

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.1"

_KNOWN_FAKE_NAMES = {
    "john doe", "jane doe", "john smith", "jane smith",
    "james smith", "mary smith", "bob smith", "alice smith",
}


def _load_intel_config() -> dict[str, Any]:
    try:
        with open("configs/app.toml", "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("intelligence", {})
    except Exception:
        return {}


_AGGREGATOR_ADAPTERS = {"adzuna", "reed", "efinancialcareers", "google_jobs"}


def _build_job_data(enriched: EnrichedJob, raw: RawJob, source: Source) -> dict[str, str]:
    company = source.employer_name or ""

    # For aggregator sources, the real employer is inside the listing payload
    if source.adapter_name in _AGGREGATOR_ADAPTERS:
        payload = raw.listing_payload
        if isinstance(payload, dict):
            co_obj = payload.get("company")
            if isinstance(co_obj, dict):
                company = co_obj.get("display_name") or company
            if company == source.employer_name:
                company = (
                    payload.get("employer_name")
                    or payload.get("companyName")
                    or payload.get("company_name")
                    or company
                )

    return {
        "title": enriched.title or raw.title_raw or "",
        "company": company,
        "location": enriched.location_text or raw.location_raw or "",
        "date_posted": str(enriched.posted_at or raw.posted_at_raw or ""),
        "description": enriched.description_text or raw.description_raw or raw.raw_text_blob or "",
    }


def _get_category(session: Session, enriched_job_id: str) -> str:
    row = session.execute(
        select(ClassificationResult.primary_taxonomy_key)
        .where(ClassificationResult.enriched_job_id == enriched_job_id)
        .order_by(ClassificationResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row or DEFAULT_CATEGORY


def _get_sub_specialism(session: Session, enriched_job_id: str) -> str | None:
    """Return the latest ClassificationResult.sub_specialism for an enriched
    job, or None if the row has no sub-specialism recorded.

    Used by the v2 HM-search template to fill the ``[function]`` slot
    (e.g. "Credit Risk", "Financial Crime", "Quantitative Trading").
    """
    row = session.execute(
        select(ClassificationResult.sub_specialism)
        .where(ClassificationResult.enriched_job_id == enriched_job_id)
        .order_by(ClassificationResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row


def _resolve_hm_searches(
    job_data: dict[str, str],
    category: str,
    sub_specialism: str | None,
    template_version: str,
) -> str:
    """Select v1 or v2 HM search strings based on the config flag.

    v2 (default): generic template rendered with company + sub_specialism
      + optional location. Falls back to v1 for this category if
      sub_specialism is empty (the [function] slot would otherwise render
      as an empty quoted string, nuking Google recall).
    v1 (legacy): per-category hand-authored search blocks.
    """
    blocks = CATEGORY_BLOCKS.get(category, CATEGORY_BLOCKS[DEFAULT_CATEGORY])

    if template_version == "v2":
        template = blocks.get("hm_search_queries_v2", "")
        func = (sub_specialism or "").strip()
        if template and func:
            return render_hm_search_template_v2(
                template=template,
                company_name=job_data.get("company", ""),
                function=func,
                location=job_data.get("location", ""),
            )
        # No sub_specialism on this classification — fall through to v1
        # so we still emit meaningful title searches for the category.
        logger.info(
            "HM template v2 requested but sub_specialism is empty — "
            "falling back to v1 blocks for category=%s",
            category,
        )

    return blocks.get("hm_search_queries_v1", blocks.get("hm_search_queries", ""))


def _build_hm_prompt(
    job_data: dict[str, str],
    category: str,
    sub_specialism: str | None = None,
    template_version: str = "v1",
) -> list[dict[str, str]]:
    """Build a focused hiring manager search prompt."""
    blocks = CATEGORY_BLOCKS.get(category, CATEGORY_BLOCKS[DEFAULT_CATEGORY])
    hm_searches = _resolve_hm_searches(
        job_data=job_data,
        category=category,
        sub_specialism=sub_specialism,
        template_version=template_version,
    )
    hm_function = blocks.get("hm_function_guidance", "")

    return [
        {"role": "system", "content": "You are a recruitment researcher. Search the web to find real people. Return valid JSON only."},
        {"role": "user", "content": f"""Find the most likely hiring manager for this role at {job_data['company']}.

Role: {job_data['title']}
Company: {job_data['company']}
Location: {job_data['location']}

Job description:
{job_data.get('description', '')[:3000]}

The hiring manager is the person the successful candidate would report into, not HR or Talent Acquisition.

Step 1: Determine the hiring manager's likely title.
- If the advert mentions a reporting line (e.g. "reporting to the Chief Credit Officer"), use that title.
- Otherwise, derive the function from the role title {hm_function} by searching the term Head of "function identified in title" or Director "function identified in title" Go one or two levels above the role seniority, but only ever return a Chief Credit Officer or Chief Risk Officer if the role being searched is a Director or Head of Title
- If the JD specifies a region such as EMEA, ignore it for the search — the hiring manager could be based anywhere.
- If the JD specifies an asset class or sub-specialism (e.g. Leveraged Finance, Real Estate), include it.
- If the role relates to information security, cyber security, security engineering, SOC, or any infosec function, the hiring manager is ultimately the CISO (Chief Information Security Officer). Always search for the CISO at the company first. The direct reporting line may be a Head of Security Engineering or similar, but always include the CISO as a candidate.

Step 2: Search LinkedIn for real people with that title at this company. Try these searches:
{hm_searches}

The hiring manager's name will NOT be in the job advert. You must search LinkedIn to find who holds the title you identified in Step 1.

Step 3: Return up to 3 candidates ranked by confidence, with name, title, and the search query that surfaced them. If you cannot confidently identify anyone, say so and explain what made it difficult.

Return JSON only:
{{"hiring_managers": [{{"name": "...", "title": "...", "confidence": "high|medium|low", "reasoning": "..."}}]}}"""},
    ]


async def generate_dossier(
    enriched_job_id: str,
    session: Session,
    force: bool = False,
) -> IntelligenceDossier:
    """Generate (or return cached) dossier for an enriched job.

    By default this reads the most recent dossier for the enriched job
    and returns it without calling the LLM if one exists with a real
    body. Pass force=True to bypass the cache and regenerate (useful
    after a prompt-version bump).
    """
    if not force:
        existing = session.execute(
            select(IntelligenceDossier)
            .where(IntelligenceDossier.enriched_job_id == enriched_job_id)
            .order_by(IntelligenceDossier.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing and (existing.core_problem or "").strip():
            logger.info(
                "Reusing cached dossier %s for enriched_job %s — skipping LLM call",
                existing.id, enriched_job_id,
            )
            return existing

    enriched = session.get(EnrichedJob, enriched_job_id)
    if not enriched:
        raise ValueError(f"EnrichedJob {enriched_job_id} not found")

    raw = session.get(RawJob, enriched.raw_job_id)
    source = session.get(Source, raw.source_id) if raw else None
    if not raw or not source:
        raise ValueError(f"Could not load RawJob/Source for enriched job {enriched_job_id}")

    category = _get_category(session, enriched_job_id)
    job_data = _build_job_data(enriched, raw, source)

    if not job_data["description"].strip():
        logger.warning("No description available for %s — dossier quality will be limited", enriched_job_id)

    messages = resolve_dossier_prompt(category, job_data)
    config = _load_intel_config()

    # Call 1: Main dossier (sections 1-7) with web search for context
    # (OpenAI only — DeepSeek has no web-search tool). Provider toggle:
    # set use_deepseek_for_dossier=true in configs/app.toml to route
    # this call to DeepSeek's reasoner model. Web search will be
    # silently dropped by the provider layer in that case; see
    # docs/intelligence-providers.md for the trade-off.
    use_deepseek_dossier = bool(config.get("use_deepseek_for_dossier", False))
    if use_deepseek_dossier:
        dossier_provider = LLMProvider.DEEPSEEK
        model = config.get("dossier_model_deepseek", "deepseek-reasoner")
    else:
        dossier_provider = LLMProvider.OPENAI
        model = config.get("dossier_model", "gpt-4o")
    result = await call_llm(
        provider=dossier_provider,
        model=model,
        messages=messages,
        temperature=config.get("temperature", 0.4),
        max_tokens=config.get("max_tokens", 8000),
        timeout_seconds=config.get("timeout_seconds", 120),
        web_search=True,
        reasoning_effort=config.get("dossier_reasoning_effort", "medium"),
        search_context_size=config.get("dossier_search_context_size", "low"),
    )
    parsed = result["parsed"]

    # Call 2: Focused hiring-manager search. Two routes, switched by the
    # `use_serpapi_hm_search` toggle in configs/app.toml:
    #
    #   true  → hm_search_serpapi.run_hm_search_via_serpapi
    #           Hits SerpApi directly with the category's pre-authored
    #           LinkedIn queries, feeds snippets to gpt-4o-mini for name
    #           extraction. ~60% cheaper and ~3× faster than the OpenAI
    #           web_search route. Requires SERPAPI_KEY in .env.
    #
    #   false → OpenAI gpt-5.2 + Responses-API web_search_preview.
    #           Model does the LinkedIn searches itself and extracts in
    #           one call. Still hard-wired to OpenAI — personal data
    #           stays on OpenAI whichever route is taken; SerpApi only
    #           returns public LinkedIn snippets, no PII sent to any
    #           non-OpenAI LLM.
    #
    # If SerpApi is enabled but SERPAPI_KEY is missing or the SerpApi
    # call raises, we fall back to the OpenAI path automatically so the
    # dossier still lands.
    use_serpapi = bool(config.get("use_serpapi_hm_search", False))
    template_version = str(config.get("hm_template_version", "v2") or "v2").lower()
    sub_specialism = _get_sub_specialism(session, enriched_job_id)
    hm_messages = _build_hm_prompt(
        job_data,
        category,
        sub_specialism=sub_specialism,
        template_version=template_version,
    )
    hm_result: dict[str, Any] | None = None
    if use_serpapi:
        try:
            from vacancysoft.intelligence.hm_search_serpapi import (
                DEFAULT_EXTRACTION_MODEL,
                DEFAULT_MAX_SEARCHES,
                run_hm_search_via_serpapi,
            )
            hm_result = await run_hm_search_via_serpapi(
                job_data=job_data,
                category=category,
                max_searches=int(config.get("hm_serpapi_max_searches", DEFAULT_MAX_SEARCHES)),
                extraction_model=config.get("hm_extraction_model", DEFAULT_EXTRACTION_MODEL),
                sub_specialism=sub_specialism,
                template_version=template_version,
            )
        except Exception as exc:
            logger.warning(
                "SerpApi HM search failed (%s) — falling back to OpenAI web_search path",
                exc,
            )
            hm_result = None

    if hm_result is None:
        # OpenAI web_search path (original, default)
        hm_model = config.get("hm_search_model", "gpt-4o")
        hm_result = await call_llm(
            provider=LLMProvider.OPENAI,
            model=hm_model,
            messages=hm_messages,
            temperature=0.2,
            max_tokens=2000,
            timeout_seconds=60,
            web_search=True,
            reasoning_effort=config.get("hm_search_reasoning_effort", "low"),
            search_context_size=config.get("hm_search_context_size", "high"),
        )
    hm_parsed = hm_result["parsed"]

    # Filter out obvious fakes and former employees
    hiring_managers = hm_parsed.get("hiring_managers") or parsed.get("hiring_managers") or []
    hiring_managers = [
        hm for hm in hiring_managers
        if isinstance(hm, dict)
        and hm.get("name", "").lower().strip() not in _KNOWN_FAKE_NAMES
        and "former" not in (hm.get("title") or "").lower()
    ]

    from vacancysoft.intelligence.pricing import compute_cost

    main_cost = compute_cost(result["model"], result["tokens_prompt"], result["tokens_completion"])
    hm_llm_cost = compute_cost(hm_result["model"], hm_result["tokens_prompt"], hm_result["tokens_completion"])
    # SerpApi path adds a usage fee (~$0.015/search) on top of the
    # LLM cost. For the OpenAI-only path this key is absent and the
    # .get() returns 0.0.
    hm_serpapi_cost = float(hm_result.get("serpapi_cost_usd", 0.0))
    hm_cost = hm_llm_cost + hm_serpapi_cost

    total_prompt = result["tokens_prompt"] + hm_result["tokens_prompt"]
    total_completion = result["tokens_completion"] + hm_result["tokens_completion"]
    total_tokens = result["tokens_total"] + hm_result["tokens_total"]
    total_latency = result["latency_ms"] + hm_result["latency_ms"]
    cost_usd = main_cost + hm_cost

    hm_breakdown_entry = {
        "call": "hm",
        "model": hm_result["model"],
        "tokens_prompt": hm_result["tokens_prompt"],
        "tokens_completion": hm_result["tokens_completion"],
        "tokens_total": hm_result["tokens_total"],
        "cost_usd": round(hm_cost, 6),
        "latency_ms": hm_result["latency_ms"],
    }
    # Diagnostic extras for the SerpApi path so cost-report can show
    # the split. Harmless on the OpenAI path — the keys are absent.
    if "serpapi_searches" in hm_result:
        hm_breakdown_entry["serpapi_searches"] = hm_result["serpapi_searches"]
        hm_breakdown_entry["serpapi_cost_usd"] = round(hm_serpapi_cost, 6)
        hm_breakdown_entry["llm_cost_usd"] = round(hm_llm_cost, 6)

    call_breakdown = [
        {
            "call": "main",
            "model": result["model"],
            "tokens_prompt": result["tokens_prompt"],
            "tokens_completion": result["tokens_completion"],
            "tokens_total": result["tokens_total"],
            "cost_usd": round(main_cost, 6),
            "latency_ms": result["latency_ms"],
        },
        hm_breakdown_entry,
    ]

    dossier = IntelligenceDossier(
        enriched_job_id=enriched_job_id,
        prompt_version=PROMPT_VERSION,
        category_used=category,
        model_used=result["model"],
        company_context=parsed.get("company_context"),
        core_problem=parsed.get("core_problem"),
        stated_vs_actual=parsed.get("stated_vs_actual"),
        spec_risk=parsed.get("spec_risk"),
        candidate_profiles=parsed.get("candidate_profiles"),
        search_booleans={"hiring_manager_boolean": parsed.get("hiring_manager_boolean", "")},
        lead_score=parsed.get("lead_score"),
        lead_score_justification=parsed.get("lead_score_justification"),
        hiring_managers=hiring_managers,
        raw_response=parsed,
        tokens_used=total_tokens,
        tokens_prompt=total_prompt,
        tokens_completion=total_completion,
        cost_usd=round(cost_usd, 6),
        call_breakdown=call_breakdown,
        latency_ms=total_latency,
    )
    session.add(dossier)
    session.commit()

    logger.info(
        "Dossier for %s [%s] — main=%s $%.4f (%dt, %dms) + hm=%s $%.4f (%dt, %dms) = $%.4f total, score=%s, HMs=%d",
        job_data["company"], category,
        result["model"], main_cost, result["tokens_total"], result["latency_ms"],
        hm_result["model"], hm_cost, hm_result["tokens_total"], hm_result["latency_ms"],
        cost_usd, parsed.get("lead_score"), len(hiring_managers),
    )
    return dossier
