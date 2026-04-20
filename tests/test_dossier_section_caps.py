"""Guard tests for dossier section caps (v1.3, 2026-04-21).

Two dossier sections were widened:
  - Section 3 (stated_vs_actual):  EXACTLY 2   → up to 4
  - Section 4 (spec_risk):         1 OR 2      → up to 4

This file pins the new caps in three places that must stay in lockstep:
  1. base_dossier.py prompt text (what the model is told)
  2. resolver.py slice bounds (what the campaign prompt sees)
  3. PROMPT_VERSION marker (segmentation in cost_report)

If any one of these drifts, the other two get out of sync and the
model's output is either truncated on the way to the campaign
(resolver cap < dossier cap) or the prompt text lies about the
shape (text says 4 but resolver only forwards 2).
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BASE_DOSSIER = ROOT / "src/vacancysoft/intelligence/prompts/base_dossier.py"
RESOLVER = ROOT / "src/vacancysoft/intelligence/prompts/resolver.py"
DOSSIER_PY = ROOT / "src/vacancysoft/intelligence/dossier.py"


class TestSectionCaps:

    def test_stated_vs_actual_cap_is_4(self) -> None:
        text = BASE_DOSSIER.read_text(encoding="utf-8")
        assert "stated_vs_actual) — UP TO 4 rows" in text
        # Frozen wording of the legacy cap must be gone so cost_report
        # segmentation on prompt_version actually reflects a real shape
        # change.
        assert "stated_vs_actual) — EXACTLY 2 rows" not in text

    def test_spec_risk_cap_is_4(self) -> None:
        text = BASE_DOSSIER.read_text(encoding="utf-8")
        assert "spec_risk) — UP TO 4 items" in text
        assert "1 OR 2 items" not in text

    def test_prompt_discourages_padding(self) -> None:
        """Raising a cap without a 'do not pad' guard encourages the
        model to pad weak items to hit the ceiling."""
        text = BASE_DOSSIER.read_text(encoding="utf-8")
        assert "do not pad with near-duplicates" in text
        assert "never pad the list to hit 4" in text

    def test_resolver_slices_match_dossier_cap(self) -> None:
        """The resolver's slice bounds must be >= the dossier cap, or
        the campaign prompt will silently drop section-3/4 rows."""
        text = RESOLVER.read_text(encoding="utf-8")
        assert "risks[:4]" in text
        assert "sva[:4]" in text
        # Legacy caps must be gone
        assert "risks[:3]" not in text
        assert "sva[:3]" not in text

    def test_prompt_version_bumped_to_v1_3(self) -> None:
        """cost_report segments by prompt_version; bumping on shape
        changes lets the operator A/B pre- and post-shape leads."""
        text = DOSSIER_PY.read_text(encoding="utf-8")
        assert 'PROMPT_VERSION = "v1.3"' in text
        assert 'PROMPT_VERSION = "v1.2"' not in text


class TestSearchContextConfigured:
    """Cost-cut: dossier_search_context_size dropped from "high" to "medium"
    alongside this shape change."""

    def test_app_toml_has_medium(self) -> None:
        import tomllib

        cfg = tomllib.load((ROOT / "configs/app.toml").open("rb"))
        assert cfg["intelligence"]["dossier_search_context_size"] == "medium"
