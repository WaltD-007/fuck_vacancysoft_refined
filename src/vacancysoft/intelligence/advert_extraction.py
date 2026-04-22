"""LLM-extract structured fields from a pasted job-advert body.

The Lead List's text-paste flow hands us the raw advert body the operator
copied from a careers site / LinkedIn post. This module runs one LLM call
to pull out title, company, location, and posted date, so the rest of the
pipeline (RawJob → EnrichedJob → ClassificationResult → ScoreResult →
ReviewQueueItem) can stay identical to the URL-paste flow it replaces.

Return shape is deliberately a drop-in for
:func:`vacancysoft.intelligence.url_scrape.scrape_advert` so the paste
route just swaps the one line:

    meta = await scrape_advert(url)          # before
    meta = await extract_advert_fields(text) # after

Fields:

- ``status``   — always ``"success"`` when this function returns. On LLM
                 failure we raise; the route surfaces a 422.
- ``title``    — the job title, cleaned. Empty string if the model can't
                 find one (the route then 422s the same way it does for
                 a title-less Playwright scrape).
- ``company``  — employer name. Empty string if missing.
- ``location`` — raw location string as the advert presents it (e.g.
                 ``"London, UK"`` or ``"Remote — US"``). Downstream
                 ``normalise_location()`` handles the city/country split.
- ``description`` — the **full pasted text**, unchanged. Not an LLM
                 summary: we keep the advert lossless so the dossier
                 prompt has everything to work with.
- ``postedDate`` — ISO date (``YYYY-MM-DD``) if the advert states one,
                 else empty string.

Config (``[intelligence]`` of ``configs/app.toml``):

- ``advert_extract_model``   — OpenAI chat model id (default
                               ``"gpt-4o-mini"``). JSON-mode reliable,
                               fast (<2 s), cheap (<$0.001/call).
- ``use_deepseek_for_advert_extract`` — bool (default ``false``). Route
                                        to DeepSeek's chat model instead.
- ``advert_extract_model_deepseek``   — used when the toggle is on;
                                        default ``"deepseek-chat"``.
"""

from __future__ import annotations

import logging
import tomllib
from typing import Any

from vacancysoft.intelligence.providers import LLMProvider, call_llm

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You extract structured metadata from job adverts.

The user will paste the full text of a single job advert (title line,
company, location, description, responsibilities, requirements — in
whatever order the source site used). Return ONLY a JSON object with
EXACTLY these four keys:

{
  "title": "<job title as written in the advert>",
  "company": "<hiring company / employer>",
  "location": "<location as written, e.g. 'London, UK' or 'New York, NY'>",
  "posted_date": "<ISO date YYYY-MM-DD if the advert states one, else ''>"
}

Rules:

- Use empty string "" for any field you cannot confidently extract.
  Do not guess — the downstream pipeline handles missing fields.
- For the title, keep the role as the advert presents it; do not
  normalise, re-capitalise, or add/remove seniority words.
- For the company, return the EMPLOYER (the org doing the hiring), not
  the recruitment agency or job-board name. If the advert is posted by
  an agency on behalf of a client and the client isn't named, return
  the agency name — the operator can correct later.
- For location, preserve the advert's form (e.g. "London, United
  Kingdom" as-is; "Remote — US" as-is). Don't split or invent.
- For posted_date, only emit an ISO date if the advert literally
  states one (e.g. "Posted: 15 April 2026" → "2026-04-15"). Never
  guess from "recently" / "fresh" phrasing.
- The description itself is NOT part of this JSON — the caller
  already has the full text.
"""


_EXPECTED_KEYS = ("title", "company", "location", "posted_date")


def _load_intel_config() -> dict[str, Any]:
    """Load the ``[intelligence]`` block from ``configs/app.toml``."""
    try:
        with open("configs/app.toml", "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("intelligence", {})
    except Exception:
        return {}


async def extract_advert_fields(advert_text: str) -> dict[str, str]:
    """LLM-parse a pasted advert body into {title, company, location,
    description, postedDate}.

    Shape matches :func:`vacancysoft.intelligence.url_scrape.scrape_advert`
    so the paste route can substitute the call without touching the
    downstream persistence code.

    Raises:
        ValueError — if the advert text is empty.
        RuntimeError — if the LLM returned no parseable JSON or tripped
                       retry exhaustion. The paste route translates this
                       into a 422.
    """
    text = (advert_text or "").strip()
    if not text:
        raise ValueError("advert_text is empty")

    config = _load_intel_config()
    use_deepseek = bool(config.get("use_deepseek_for_advert_extract", False))
    if use_deepseek:
        provider = LLMProvider.DEEPSEEK
        model = config.get("advert_extract_model_deepseek", "deepseek-chat")
    else:
        provider = LLMProvider.OPENAI
        model = config.get("advert_extract_model", "gpt-4o-mini")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    result = await call_llm(
        provider=provider,
        model=model,
        messages=messages,
        # Deterministic extraction — no creativity wanted.
        temperature=0.0,
        # JSON-mode keeps the model on-schema. Both OpenAI and DeepSeek
        # honour this via call_llm; see providers.py.
        response_format={"type": "json_object"},
        # Tiny budget: the output is four short strings.
        max_tokens=400,
        # Most adverts resolve in <2s; give headroom for cold starts.
        timeout_seconds=30,
    )

    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"advert_extract: LLM response was not a JSON object "
            f"(model={model!r}, raw={result.get('raw_content')!r})"
        )

    # Coerce every expected key to a clean string — JSON-mode models
    # occasionally return null or a number for missing fields.
    def _clean(key: str) -> str:
        val = parsed.get(key)
        if val is None:
            return ""
        return str(val).strip()

    title = _clean("title")
    company = _clean("company")
    location = _clean("location")
    posted_date = _clean("posted_date")

    logger.info(
        "advert_extract model=%s title=%r company=%r location=%r "
        "posted_date=%r tokens=%s latency_ms=%s",
        result.get("model"),
        title,
        company,
        location,
        posted_date,
        result.get("tokens_total"),
        result.get("latency_ms"),
    )

    return {
        "status": "success",
        "title": title,
        "company": company,
        "location": location,
        # Full pasted text — lossless, exactly what scrape_advert would
        # have written into RawJob.description_raw.
        "description": text,
        "postedDate": posted_date,
        # Diagnostic bread-crumbs the route writes into
        # RawJob.listing_payload.extraction_meta — useful when debugging
        # a mis-extracted field later.
        "model": result.get("model"),
        "tokens_total": result.get("tokens_total"),
        "latency_ms": result.get("latency_ms"),
    }
