"""Guard tests for the HM candidate cap.

Three prompt locations independently say "up to N real people /
candidates". They must stay in lockstep: if one drifts to a different
number the three HM paths (dossier main prompt, OpenAI web_search
fallback, SerpApi extraction) disagree and you get inconsistent list
lengths across providers.

This file pins them all at 6. Bump in one place → test fails → bump
the other two or revert.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BASE_DOSSIER = ROOT / "src/vacancysoft/intelligence/prompts/base_dossier.py"
DOSSIER_PY = ROOT / "src/vacancysoft/intelligence/dossier.py"
SERPAPI_PY = ROOT / "src/vacancysoft/intelligence/hm_search_serpapi.py"


class TestHiringManagerCap:
    """All three HM prompt surfaces must cap at 6."""

    def test_base_dossier_says_up_to_6(self) -> None:
        text = BASE_DOSSIER.read_text(encoding="utf-8")
        assert "Return up to 6 candidates" in text
        assert "Return up to 3 candidates" not in text

    def test_dossier_py_fallback_says_up_to_6(self) -> None:
        """The OpenAI web_search fallback prompt (used when SerpApi is off
        or unavailable) lives inline in dossier.py::_build_hm_prompt."""
        text = DOSSIER_PY.read_text(encoding="utf-8")
        assert "Return up to 6 candidates" in text
        assert "Return up to 3 candidates" not in text

    def test_serpapi_system_says_up_to_6(self) -> None:
        """SerpApi extraction system prompt — caps the count on the
        cheap gpt-4o-mini extraction path."""
        from vacancysoft.intelligence.hm_search_serpapi import EXTRACTION_SYSTEM
        assert "up to 6 real people" in EXTRACTION_SYSTEM
        assert "up to 3 real people" not in EXTRACTION_SYSTEM

    def test_serpapi_user_prompt_says_up_to_6(self) -> None:
        """SerpApi extraction user prompt — the actual instruction
        handed to the extraction model per lead."""
        text = SERPAPI_PY.read_text(encoding="utf-8")
        assert "identify up to 6 real people" in text
        assert "identify up to 3 real people" not in text

    def test_prompts_discourage_padding(self) -> None:
        """Having raised the cap to 6 we must tell the model to stop
        short when it would otherwise pad with low-confidence names."""
        base = BASE_DOSSIER.read_text(encoding="utf-8")
        dossier = DOSSIER_PY.read_text(encoding="utf-8")
        serpapi = SERPAPI_PY.read_text(encoding="utf-8")
        needle = "do not pad the list with low-confidence filler"
        assert needle in base
        assert needle in dossier
        assert needle in serpapi
