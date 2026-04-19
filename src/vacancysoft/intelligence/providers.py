"""LLM provider abstraction for Prospero.

Two providers currently supported:

- ``OPENAI`` — the original path used by ``client.call_chat``. Supports
  the Responses API with web_search and the Chat Completions API with
  reasoning_effort. Used for hiring-manager search (handles named real
  individuals — privacy requirement) and, by default, for dossier and
  campaign generation too.
- ``DEEPSEEK`` — lower-cost alternative using DeepSeek's OpenAI-compatible
  ``/v1/chat/completions`` endpoint. Can be routed to for dossier
  and campaign generation by flipping the two toggles in
  ``configs/app.toml``. Not used for hiring-manager search.

A single entry point :func:`call_llm` accepts a ``provider`` parameter
and routes to the correct backend. Both backends return the same dict
shape as :func:`vacancysoft.intelligence.client.call_chat` — callers
substitute one for the other without any downstream shape changes.

Config toggles (in ``[intelligence]`` of ``configs/app.toml``):

- ``use_deepseek_for_dossier``  — ``bool`` (default ``false``).
  When true, the main dossier analysis call uses
  ``dossier_model_deepseek`` (default ``"deepseek-reasoner"``).
- ``use_deepseek_for_campaign`` — ``bool`` (default ``false``).
  When true, campaign email generation uses
  ``campaign_model_deepseek`` (default ``"deepseek-reasoner"``) with
  fallback to ``campaign_fallback_model_deepseek`` (default
  ``"deepseek-chat"``).

Hiring-manager search (``dossier.py``, call 2) is hard-wired to
``OPENAI`` and has no config toggle — moving named-individual lookups
to a different provider is a policy decision that should happen in
review, not by a config flip.

See ``docs/intelligence-providers.md`` for the full switch guide and
the cost / feature trade-off tables.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from enum import Enum
from typing import Any

from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from vacancysoft.intelligence.client import call_chat as _openai_call_chat

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Which LLM backend to call."""
    OPENAI = "openai"
    DEEPSEEK = "deepseek"


# OpenAI-compatible endpoint for DeepSeek. Override via env var if you
# need to route through a proxy.
_DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

# Retry config — mirrors client.py's shape deliberately so both
# providers behave identically on transient errors.
_RETRYABLE = (APITimeoutError, RateLimitError)
_MAX_RETRIES = 3
_BACKOFF = (2, 5, 15)

# Per-process cached DeepSeek client. The OpenAI client is managed by
# ``client.py``'s own ``_get_client`` — we don't duplicate that cache.
_deepseek_client: AsyncOpenAI | None = None


def _get_deepseek_client() -> AsyncOpenAI:
    """Lazily construct the DeepSeek AsyncOpenAI client.

    DeepSeek's API is OpenAI-compatible, so we reuse the ``openai``
    Python SDK with a different ``base_url`` and api key. The client
    is cached for the process lifetime.
    """
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Add it to .env before enabling "
                "use_deepseek_for_dossier / use_deepseek_for_campaign."
            )
        _deepseek_client = AsyncOpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE_URL)
    return _deepseek_client


async def call_llm(
    *,
    provider: LLMProvider,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 8000,
    timeout_seconds: float = 120,
    response_format: dict[str, str] | None = None,
    web_search: bool = False,
    reasoning_effort: str | None = None,
    search_context_size: str = "high",
) -> dict[str, Any]:
    """Provider-agnostic chat completion call.

    Returns the same dict shape as ``client.call_chat``, so call sites
    can route between providers just by flipping ``provider``:

        result = await call_llm(
            provider=LLMProvider.DEEPSEEK if use_deepseek else LLMProvider.OPENAI,
            model=chosen_model,
            messages=messages,
            ...
        )

    Return keys: ``parsed``, ``raw_content``, ``model``,
    ``tokens_prompt``, ``tokens_completion``, ``tokens_total``,
    ``finish_reason``, ``latency_ms``. The DeepSeek branch also
    includes ``reasoning_content`` when calling ``deepseek-reasoner``
    (the model's chain-of-thought, not parsed as JSON).

    **Caveats when ``provider == DEEPSEEK``**:

    - ``web_search=True`` is silently dropped with a warning —
      DeepSeek has no web-search tool equivalent. The call goes to
      chat completions and the model answers from its training data
      only. If web-augmented context matters for this call, keep it
      on OpenAI.
    - ``reasoning_effort`` is silently dropped (debug-logged).
      DeepSeek's reasoner uses a fixed internal reasoning budget; no
      knob is exposed.
    - ``search_context_size`` is dropped for the same reason.
    - ``response_format={"type": "json_object"}`` **is** supported by
      DeepSeek's OpenAI-compatible endpoint and passes through
      unchanged.
    """
    if provider == LLMProvider.OPENAI:
        # Delegate to the original client so we reuse its retry logic,
        # Responses-API handling, reasoning-effort plumbing, and
        # everything else that has been battle-tested there.
        return await _openai_call_chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            response_format=response_format,
            web_search=web_search,
            reasoning_effort=reasoning_effort,
            search_context_size=search_context_size,
        )

    if provider == LLMProvider.DEEPSEEK:
        if web_search:
            logger.warning(
                "call_llm(provider=DEEPSEEK, model=%r) — web_search=True "
                "silently dropped; DeepSeek has no web-search tool. Output "
                "will lack web-augmented context. If this call requires "
                "web context, keep it on OPENAI.",
                model,
            )
        if reasoning_effort:
            logger.debug(
                "call_llm(provider=DEEPSEEK, model=%r) — reasoning_effort=%r "
                "dropped; DeepSeek reasoner uses a fixed reasoning budget.",
                model, reasoning_effort,
            )
        return await _call_deepseek(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            response_format=response_format,
        )

    raise ValueError(f"Unknown provider: {provider!r}")


async def _call_deepseek(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
    response_format: dict[str, str] | None,
) -> dict[str, Any]:
    """Chat completion against DeepSeek's OpenAI-compatible endpoint.

    The reasoner model (``deepseek-reasoner``) returns both a
    ``content`` field (the final answer, which we parse as JSON) and
    a ``reasoning_content`` field (the chain-of-thought, which we
    keep in the result dict for diagnostics but do not parse).

    Retry policy mirrors ``client.call_chat``: up to 3 attempts,
    backoff 2s / 5s / 15s, retryable on ``APITimeoutError`` and
    ``RateLimitError``.
    """
    client = _get_deepseek_client()

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            t0 = time.monotonic()

            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "timeout": timeout_seconds,
            }
            # DeepSeek's reasoner model ignores temperature (fixed at 1.0
            # internally per their docs); chat models honour it.
            # Pass it unconditionally — the reasoner silently ignores it.
            kwargs["temperature"] = temperature
            if response_format:
                kwargs["response_format"] = response_format

            resp = await client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            message = choice.message
            content = message.content or ""
            # Reasoner model only: capture the chain-of-thought.
            reasoning_content = getattr(message, "reasoning_content", None) or ""
            usage = resp.usage

            try:
                parsed = json.loads(content) if content else {"_raw_text": ""}
            except json.JSONDecodeError:
                parsed = {"_raw_text": content}

            result = {
                "parsed": parsed,
                "raw_content": content,
                "model": resp.model,
                "tokens_prompt": usage.prompt_tokens if usage else 0,
                "tokens_completion": usage.completion_tokens if usage else 0,
                "tokens_total": usage.total_tokens if usage else 0,
                "finish_reason": choice.finish_reason,
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }
            if reasoning_content:
                result["reasoning_content"] = reasoning_content
            return result

        except _RETRYABLE as exc:
            last_err = exc
            wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            logger.warning(
                "DeepSeek call failed (attempt %d): %s — retrying in %ds",
                attempt + 1, exc, wait,
            )
            await asyncio.sleep(wait)

    raise RuntimeError(f"DeepSeek call failed after {_MAX_RETRIES} retries: {last_err}")
