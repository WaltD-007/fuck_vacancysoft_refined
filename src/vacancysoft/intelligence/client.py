from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from openai import AsyncOpenAI, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)

_RETRYABLE = (APITimeoutError, RateLimitError)
_MAX_RETRIES = 3
_BACKOFF = (2, 5, 15)

# Shared client — reused across calls within the same process
_shared_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _shared_client
    if _shared_client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _shared_client = AsyncOpenAI(api_key=api_key)
    return _shared_client


async def call_chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 8000,
    timeout_seconds: float = 120,
    response_format: dict[str, str] | None = None,
    web_search: bool = False,
) -> dict[str, Any]:
    """Call OpenAI API. Uses the Responses API with web search when web_search=True,
    otherwise falls back to the Chat Completions API."""
    client = _get_client()

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            t0 = time.monotonic()

            if web_search:
                result = await _call_responses_api(
                    client, model=model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
            else:
                result = await _call_completions_api(
                    client, model=model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                    response_format=response_format,
                )

            result["latency_ms"] = int((time.monotonic() - t0) * 1000)
            return result

        except _RETRYABLE as exc:
            last_err = exc
            wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            logger.warning("OpenAI call failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
            import asyncio
            await asyncio.sleep(wait)

    raise RuntimeError(f"OpenAI call failed after {_MAX_RETRIES} retries: {last_err}")


async def _call_completions_api(
    client: AsyncOpenAI, *, model: str, messages: list[dict[str, str]],
    temperature: float, max_tokens: int, timeout_seconds: float,
    response_format: dict[str, str] | None,
) -> dict[str, Any]:
    # GPT-5 / o1 / o3 reasoning-style models require max_completion_tokens
    # and do not accept a custom temperature.
    model_lower = model.lower()
    is_reasoning = (
        model_lower.startswith("gpt-5")
        or model_lower.startswith("o1")
        or model_lower.startswith("o3")
        or model_lower.startswith("o4")
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "timeout": timeout_seconds,
    }
    if is_reasoning:
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
        kwargs["temperature"] = temperature
    if response_format:
        kwargs["response_format"] = response_format

    resp = await client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    content = choice.message.content or ""
    usage = resp.usage

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"_raw_text": content}

    return {
        "parsed": parsed,
        "raw_content": content,
        "model": resp.model,
        "tokens_prompt": usage.prompt_tokens if usage else 0,
        "tokens_completion": usage.completion_tokens if usage else 0,
        "tokens_total": usage.total_tokens if usage else 0,
        "finish_reason": choice.finish_reason,
    }


async def _call_responses_api(
    client: AsyncOpenAI, *, model: str, messages: list[dict[str, str]],
    temperature: float, max_tokens: int, timeout_seconds: float,
) -> dict[str, Any]:
    """Use the Responses API with web_search_preview tool enabled."""

    input_parts = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            input_parts.append({"role": "developer", "content": msg["content"]})
        else:
            input_parts.append({"role": role, "content": msg["content"]})

    resp = await client.responses.create(
        model=model,
        input=input_parts,
        tools=[{
            "type": "web_search_preview",
            "search_context_size": "high",
        }],
        temperature=temperature,
        max_output_tokens=max_tokens,
        timeout=timeout_seconds,
    )

    content = resp.output_text or ""
    usage = resp.usage

    # Strip markdown code fences if present
    clean = content.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    if clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        parsed = {"_raw_text": content}

    return {
        "parsed": parsed,
        "raw_content": content,
        "model": resp.model,
        "tokens_prompt": usage.input_tokens if usage else 0,
        "tokens_completion": usage.output_tokens if usage else 0,
        "tokens_total": (usage.input_tokens + usage.output_tokens) if usage else 0,
        "finish_reason": "stop",
    }
