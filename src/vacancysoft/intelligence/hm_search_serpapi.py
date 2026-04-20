"""Hiring-manager search via SerpApi + cheap LLM extraction.

Alternative to the default ``dossier.py::_build_hm_prompt`` path
(OpenAI gpt-5.2 with Responses-API ``web_search_preview``). Cuts the
dossier's per-lead cost by ~75% on the HM step by:

1. Hitting SerpApi's Google Search API directly with the category's
   pre-authored LinkedIn ``site:linkedin.com/in`` queries (previously
   the model ran these itself via the Responses API).
2. Collecting the top ~10 snippets per query into a small context
   (~a few hundred tokens, not ~30k).
3. Passing that compact context to ``gpt-4o-mini`` with the same
   extraction instructions.

Routing is controlled by the ``use_serpapi_hm_search`` toggle in the
``[intelligence]`` block of ``configs/app.toml``. When false, the
original OpenAI-only path in ``dossier.py`` runs unchanged.

Per-lead cost breakdown (observed on a Vanquis test lead using the
old path):

    Old path (gpt-5.2 + web_search_preview at "high"):
      prompt     ~35,000 tokens @ $1.25/1M = $0.044
      completion  ~2,000 tokens @ $10/1M   = $0.020
      total                                  $0.064

    New path (this module, 3 SerpApi searches + gpt-4o-mini)
    [default; flip max_searches to 5 for recall-first]:

      SerpApi     3 searches × ~$0.015     = $0.045
      LLM prompt   ~1,800 tokens @ $0.15/1M = $0.0003
      LLM compl      ~230 tokens @ $0.60/1M = $0.0001
      total                                  $0.046

    Saving: ~$0.018 per lead (~28%). Latency ~11s vs baseline ~56s.

    Smoke test against Vanquis (lead where the OpenAI baseline
    returned zero HMs): 5 searches surfaced 3 real candidates
    including the actual Head of Credit Risk at high confidence.
    The quality win from going 3 → 5 searches is worth the extra
    ~$0.03/lead on recall-critical use-cases; default stays at 3
    for cost-sensitive runs.

Latency should drop too — SerpApi is ~1s per query, gpt-4o-mini is
~3-5s, so total ~10-15s (vs observed ~56s for the old path).

Returns the same ``list[dict]`` shape as the old path
(``[{"name": ..., "title": ..., "confidence": ..., "reasoning": ...}]``)
so ``generate_dossier`` and the DB schema are untouched.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from vacancysoft.intelligence.client import call_chat
from vacancysoft.intelligence.prompts.category_blocks import (
    CATEGORY_BLOCKS,
    DEFAULT_CATEGORY,
    render_hm_search_template_v2,
)

logger = logging.getLogger(__name__)


SERPAPI_URL = "https://serpapi.com/search"
# Max number of SerpApi queries per HM lookup. Default 3 covers the
# top "head of X / director of X" patterns (the first 3 queries in
# every category block). 5 catches more edge cases but typically
# doubles SerpApi spend without a matching recall gain — see the
# Vanquis smoke test results in commit message / docs.
DEFAULT_MAX_SEARCHES = 3
# Results pulled back per SerpApi call. 10 is plenty — we only feed
# the (title, snippet) pairs to the LLM, not full pages.
RESULTS_PER_SEARCH = 10
# Extraction model. gpt-4o-mini is cheap and non-reasoning — good fit
# for structured extraction from short text. Override via the
# `hm_extraction_model` config key.
DEFAULT_EXTRACTION_MODEL = "gpt-4o-mini"

SERPAPI_TIMEOUT = 20
EXTRACTION_TIMEOUT = 60


EXTRACTION_SYSTEM = (
    "You are a recruitment researcher. From the search-result snippets below, "
    "identify up to 3 real people who are the most likely hiring manager for "
    "the role described. Return valid JSON only."
)


def _parse_search_queries(raw: str) -> list[str]:
    """Strip the 'Search N:' prefix from the category_blocks templates.

    The templates look like::

        Search 1: "[company name]" "head of credit" site:linkedin.com/in
        Search 2: ...

    We want just the query string so we can pass it to SerpApi.
    """
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Split on the first colon after "Search N"
        if line.lower().startswith("search") and ":" in line:
            q = line.split(":", 1)[1].strip()
            if q:
                out.append(q)
    return out


def _substitute(query: str, company: str, function: str | None) -> str:
    """Fill [company name] and [function] placeholders in a template query."""
    q = query.replace("[company name]", company)
    if function:
        q = q.replace("[function]", function)
    return q


async def _run_serpapi_search(
    client: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    num_results: int,
) -> list[dict[str, Any]]:
    """Run a single Google search via SerpApi. Returns organic results.

    Silently returns [] on any transport-level error — caller catches
    per-query failures and moves on, rather than aborting the whole
    HM lookup if one search hits a 429.
    """
    params = {
        "engine": "google",
        "q": query,
        "num": num_results,
        "api_key": api_key,
    }
    try:
        resp = await client.get(SERPAPI_URL, params=params, timeout=SERPAPI_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(
                "SerpApi HM search returned HTTP %d for %r: %s",
                resp.status_code, query, resp.text[:200],
            )
            return []
        data = resp.json()
        return data.get("organic_results", []) or []
    except Exception as exc:
        logger.warning("SerpApi HM search failed for %r: %s", query, exc)
        return []


def _format_snippets_for_llm(
    all_results: list[tuple[str, list[dict[str, Any]]]],
) -> str:
    """Condense multi-query SerpApi output into a short text block.

    Only keeps fields the extraction model actually uses: title and
    snippet. LinkedIn result titles typically have the form
    ``"Firstname Lastname - Head of Credit at Bank | LinkedIn"`` — the
    model extracts directly from that. Full URLs are dropped; snippets
    are included because they often contain the person's current title.
    """
    blocks: list[str] = []
    for query, results in all_results:
        if not results:
            continue
        blocks.append(f"Query: {query}")
        for r in results[:RESULTS_PER_SEARCH]:
            title = (r.get("title") or "").strip()
            snippet = (r.get("snippet") or "").strip()
            if not title and not snippet:
                continue
            if snippet:
                # Trim long snippets — we care about current-role signal, not bio
                if len(snippet) > 200:
                    snippet = snippet[:200] + "…"
                blocks.append(f"  - {title} — {snippet}")
            else:
                blocks.append(f"  - {title}")
        blocks.append("")
    return "\n".join(blocks) if blocks else "(no search results)"


def _build_extraction_prompt(
    *,
    job_data: dict[str, str],
    category: str,
    snippets_block: str,
) -> list[dict[str, str]]:
    """The LLM prompt for name/title/confidence extraction from snippets."""
    blocks = CATEGORY_BLOCKS.get(category, CATEGORY_BLOCKS[DEFAULT_CATEGORY])
    hm_function = blocks.get("hm_function_guidance", "")
    user = f"""Find the most likely hiring manager for this role at {job_data['company']}.

Role: {job_data['title']}
Company: {job_data['company']}
Location: {job_data['location']}

Job description (first 2000 chars):
{job_data.get('description', '')[:2000]}

The hiring manager is the person the successful candidate would report into, not HR or Talent Acquisition.

Below are LinkedIn search-result snippets. From these snippets, identify up to 3 real people most likely to be the hiring manager. Use the title signals to judge.

Step 1: Derive the target title.
- If the advert mentions a reporting line (e.g. "reporting to the Chief Credit Officer"), use that title.
- Otherwise, derive the function from the role title {hm_function}. Go one or two levels above the role seniority. If the advertised role is itself a Director or Head of, a Chief title is plausible; otherwise stick to Head of / Director level.

Step 2: Scan the snippets for real names whose current title matches your derived target. Skip former employees and anyone described as "ex-…" or "previously…". Ignore HR / Talent Acquisition / People team titles.

Step 3: For each candidate, assign confidence:
- high: exact title match, current role, at the target company
- medium: adjacent title OR current role but title is close-but-not-exact
- low: plausible but uncertain — e.g. one of several possible reporting lines

If you cannot confidently identify anyone, return `{{"hiring_managers": []}}` and briefly explain in `reasoning` of the empty list why.

Return JSON only:
{{"hiring_managers": [{{"name": "...", "title": "...", "confidence": "high|medium|low", "reasoning": "..."}}]}}

Search snippets:
{snippets_block}
"""
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


async def run_hm_search_via_serpapi(
    *,
    job_data: dict[str, str],
    category: str,
    max_searches: int = DEFAULT_MAX_SEARCHES,
    extraction_model: str = DEFAULT_EXTRACTION_MODEL,
    sub_specialism: str | None = None,
    template_version: str = "v1",
) -> dict[str, Any]:
    """Drop-in replacement for the OpenAI + web_search_preview HM path.

    Returns the same dict shape as ``client.call_chat``:

        {
            "parsed":            {"hiring_managers": [...]},
            "raw_content":       the LLM's raw response,
            "model":             the extraction model used,
            "tokens_prompt":     int,
            "tokens_completion": int,
            "tokens_total":      int,
            "finish_reason":     str,
            "latency_ms":        int,
            "serpapi_searches":  int,          # new field: how many searches we ran
            "serpapi_cost_usd":  float,        # estimated — SerpApi bill varies per plan
        }

    so ``generate_dossier`` can consume it without branching. Raises
    RuntimeError if ``SERPAPI_KEY`` is missing (caller should fall
    back to the OpenAI path if this happens at runtime).
    """
    t0 = time.monotonic()

    api_key = os.getenv("SERPAPI_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "SERPAPI_KEY is not set. Either unset use_serpapi_hm_search "
            "or add the key to .env."
        )

    blocks = CATEGORY_BLOCKS.get(category, CATEGORY_BLOCKS[DEFAULT_CATEGORY])
    hm_function = blocks.get("hm_function_guidance", "") or ""
    # Strip the enclosing parens from hm_function_guidance so it's usable as
    # a substitution string: "(e.g. Credit Risk, …)" → "Credit Risk, …"
    hm_function_clean = hm_function.strip()
    if hm_function_clean.startswith("(") and hm_function_clean.endswith(")"):
        hm_function_clean = hm_function_clean[1:-1].strip()

    # v2 (default): render the generic template with the real sub_specialism
    # + optional location before handing to SerpApi. Falls back to v1 if
    # sub_specialism is empty so we don't emit dud "head of " queries.
    # v1: use the category's hand-authored blocks, substituting
    # hm_function_clean into any [function] slot (legacy behaviour).
    if template_version == "v2" and (sub_specialism or "").strip():
        v2_template = blocks.get("hm_search_queries_v2", "")
        hm_searches_raw = render_hm_search_template_v2(
            template=v2_template,
            company_name=job_data.get("company", ""),
            function=(sub_specialism or "").strip(),
            location=job_data.get("location", ""),
        )
        queries_raw = _parse_search_queries(hm_searches_raw)[:max_searches]
        # Company + function + location already rendered; no further
        # substitution needed.
        queries = queries_raw
    else:
        if template_version == "v2":
            logger.info(
                "HM template v2 requested but sub_specialism is empty — "
                "falling back to v1 SerpApi queries for category=%s",
                category,
            )
        hm_searches_raw = blocks.get(
            "hm_search_queries_v1", blocks.get("hm_search_queries", "")
        )
        queries_raw = _parse_search_queries(hm_searches_raw)[:max_searches]
        queries = [
            _substitute(q, job_data["company"], hm_function_clean)
            for q in queries_raw
        ]

    if not queries:
        logger.warning("No search queries available for category %r — returning empty HM list", category)
        return _empty_result(extraction_model, t0, searches_run=0)

    # ── Pull snippets in parallel ─────────────────────────────────────
    all_results: list[tuple[str, list[dict[str, Any]]]] = []
    async with httpx.AsyncClient() as client:
        import asyncio
        tasks = [
            _run_serpapi_search(client, query=q, api_key=api_key, num_results=RESULTS_PER_SEARCH)
            for q in queries
        ]
        results_per_query = await asyncio.gather(*tasks)
        for q, res in zip(queries, results_per_query):
            all_results.append((q, res))

    searches_run = len([r for _, r in all_results if r])
    snippets_block = _format_snippets_for_llm(all_results)

    # ── Extract names via cheap LLM ───────────────────────────────────
    messages = _build_extraction_prompt(
        job_data=job_data, category=category, snippets_block=snippets_block,
    )
    extraction = await call_chat(
        model=extraction_model,
        messages=messages,
        temperature=0.2,
        max_tokens=1500,
        timeout_seconds=EXTRACTION_TIMEOUT,
        response_format={"type": "json_object"},
    )

    # ── Estimate SerpApi cost ─────────────────────────────────────────
    # Production plan: $75/mo for 5,000 searches = $0.015 per search.
    # Developer plan: $50/mo for 5,000 = $0.010. Override via env
    # SERPAPI_COST_PER_SEARCH if your plan differs.
    cost_per_search = float(os.getenv("SERPAPI_COST_PER_SEARCH", "0.015"))
    serpapi_cost = round(searches_run * cost_per_search, 6)

    return {
        "parsed": extraction["parsed"],
        "raw_content": extraction["raw_content"],
        "model": extraction["model"],
        "tokens_prompt": extraction["tokens_prompt"],
        "tokens_completion": extraction["tokens_completion"],
        "tokens_total": extraction["tokens_total"],
        "finish_reason": extraction["finish_reason"],
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "serpapi_searches": searches_run,
        "serpapi_cost_usd": serpapi_cost,
    }


def _empty_result(model: str, t0: float, *, searches_run: int) -> dict[str, Any]:
    """Fallback result when the search phase produces nothing usable."""
    return {
        "parsed": {"hiring_managers": []},
        "raw_content": "",
        "model": model,
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": 0,
        "finish_reason": "skipped",
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "serpapi_searches": searches_run,
        "serpapi_cost_usd": 0.0,
    }
