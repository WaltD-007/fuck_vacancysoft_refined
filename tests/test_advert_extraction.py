"""Unit tests for intelligence.advert_extraction.

Mocks the shared ``call_llm`` so we don't hit a real provider — we're
asserting the wrapper's return-shape contract (drop-in for
``scrape_advert``), not model quality.
"""

from __future__ import annotations

import pytest

from vacancysoft.intelligence import advert_extraction


_ADVERT = (
    "Senior Credit Risk Analyst — Example Bank, London, UK.\n\n"
    "We are hiring a senior credit risk analyst to cover our wholesale "
    "book. Must have 5+ years of counterparty credit risk experience."
)


@pytest.fixture
def patched_call_llm(monkeypatch):
    """Patch providers.call_llm inside the advert_extraction module."""
    state = {"last_kwargs": None, "result": None}

    async def fake_call_llm(**kwargs):
        state["last_kwargs"] = kwargs
        return state["result"]

    monkeypatch.setattr(advert_extraction, "call_llm", fake_call_llm)
    return state


@pytest.mark.asyncio
async def test_extract_returns_scrape_advert_shape(patched_call_llm):
    """Happy path: LLM returns valid JSON → the wrapper yields a dict
    with the same keys scrape_advert() emits.
    """
    patched_call_llm["result"] = {
        "parsed": {
            "title": "Senior Credit Risk Analyst",
            "company": "Example Bank",
            "location": "London, UK",
            "posted_date": "2026-04-10",
        },
        "raw_content": "{...}",
        "model": "gpt-4o-mini",
        "tokens_prompt": 120,
        "tokens_completion": 60,
        "tokens_total": 180,
        "finish_reason": "stop",
        "latency_ms": 820,
    }

    out = await advert_extraction.extract_advert_fields(_ADVERT)

    # Drop-in contract with scrape_advert
    assert out["status"] == "success"
    assert out["title"] == "Senior Credit Risk Analyst"
    assert out["company"] == "Example Bank"
    assert out["location"] == "London, UK"
    assert out["postedDate"] == "2026-04-10"
    # Lossless description — the full pasted text
    assert out["description"] == _ADVERT

    # Diagnostic breadcrumbs the route writes into listing_payload
    assert out["model"] == "gpt-4o-mini"
    assert out["tokens_total"] == 180
    assert out["latency_ms"] == 820


@pytest.mark.asyncio
async def test_extract_coerces_missing_fields_to_empty_string(patched_call_llm):
    """LLM omits keys / returns null → wrapper substitutes empty string."""
    patched_call_llm["result"] = {
        "parsed": {"title": "Risk Officer", "company": None},
        "raw_content": "{...}",
        "model": "gpt-4o-mini",
        "tokens_total": 90,
        "latency_ms": 400,
    }

    out = await advert_extraction.extract_advert_fields(_ADVERT)

    assert out["title"] == "Risk Officer"
    assert out["company"] == ""
    assert out["location"] == ""
    assert out["postedDate"] == ""


@pytest.mark.asyncio
async def test_extract_raises_on_non_dict_parsed(patched_call_llm):
    """LLM returned something that wasn't a JSON object → RuntimeError."""
    patched_call_llm["result"] = {
        "parsed": None,  # e.g. provider fallback path that couldn't parse
        "raw_content": "some garbage",
        "model": "gpt-4o-mini",
    }

    with pytest.raises(RuntimeError):
        await advert_extraction.extract_advert_fields(_ADVERT)


@pytest.mark.asyncio
async def test_extract_rejects_empty_text(patched_call_llm):
    """Empty string → ValueError before we spend tokens."""
    with pytest.raises(ValueError):
        await advert_extraction.extract_advert_fields("   ")


@pytest.mark.asyncio
async def test_extract_passes_json_mode_to_call_llm(patched_call_llm):
    """The wrapper MUST set response_format={"type": "json_object"} —
    otherwise the model is free to wrap the JSON in prose and our
    json.loads breaks.
    """
    patched_call_llm["result"] = {
        "parsed": {
            "title": "x",
            "company": "y",
            "location": "z",
            "posted_date": "",
        },
        "raw_content": "{}",
        "model": "gpt-4o-mini",
        "tokens_total": 10,
        "latency_ms": 100,
    }

    await advert_extraction.extract_advert_fields(_ADVERT)

    kwargs = patched_call_llm["last_kwargs"]
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["temperature"] == 0.0
    # System + user messages, in order
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"
    assert kwargs["messages"][1]["content"] == _ADVERT
