"""Tests for the v2 campaign prompt template + rollback flag.

Covers:
  - v2 is structurally distinct from v1 (new phrases present,
    v1-only phrases absent)
  - resolve_campaign_prompt(template_version="v2") routes to V2
  - resolve_campaign_prompt(template_version="v1") routes to V1
  - default selection is v2
  - v2 does NOT need {outreach_angle} — unused kwargs silently ignored
  - all placeholders used by v2 are satisfied by resolve_campaign_prompt's
    kwargs (no KeyError at render time)
"""

from __future__ import annotations

from vacancysoft.intelligence.prompts.base_campaign import (
    CAMPAIGN_SYSTEM,
    CAMPAIGN_TEMPLATE,
    CAMPAIGN_TEMPLATE_V1,
    CAMPAIGN_TEMPLATE_V2,
)
from vacancysoft.intelligence.prompts.resolver import resolve_campaign_prompt


# ── Fixtures ───────────────────────────────────────────────────────

def _job() -> dict[str, str]:
    return {
        "title": "Head of Credit Risk",
        "company": "Barclays",
        "location": "London",
    }


def _dossier() -> dict[str, object]:
    return {
        "company_context": "Barclays is repositioning its credit business...",
        "core_problem": "Senior credit risk leader departed, coverage gap across wholesale...",
        "stated_vs_actual": [
            {"jd_asks_for": "15 yrs risk experience", "business_likely_needs": "track record in transition lending"},
        ],
        "spec_risk": [
            {"severity": "high", "risk": "Over-specification", "explanation": "Asks for FRTB experience the UK market largely lacks."},
        ],
        "candidate_profiles": [
            {"label": "Profile A", "background": "Senior director ex-HSBC wholesale credit", "fit_reason": "Sector overlap", "outcomes": "Built out retail desk"},
        ],
        "lead_score_justification": "High-value lead; senior replacement, real hiring difficulty.",
        "hiring_managers": [
            {"name": "Jane Doe", "title": "Chief Risk Officer", "confidence": "high"},
        ],
    }


# ── Structural differences between v1 and v2 ──────────────────────

class TestTemplateVersions:

    def test_v1_and_v2_are_different(self) -> None:
        assert CAMPAIGN_TEMPLATE_V1 != CAMPAIGN_TEMPLATE_V2

    def test_v1_alias_points_at_v1(self) -> None:
        """Back-compat alias: `CAMPAIGN_TEMPLATE` is still the legacy v1."""
        assert CAMPAIGN_TEMPLATE is CAMPAIGN_TEMPLATE_V1

    def test_v2_has_new_phrases(self) -> None:
        # v2-specific phrases authored in the xlsx — confirms we
        # copied the right content in, not just a tweak of v1.
        assert "TONE CONTROLS CONTENT" in CAMPAIGN_TEMPLATE_V2
        assert "Campaign anchors" in CAMPAIGN_TEMPLATE_V2
        assert "Tone -> source mapping" in CAMPAIGN_TEMPLATE_V2
        assert "Sequence 1 — Initial outreach" in CAMPAIGN_TEMPLATE_V2
        assert "Sequence 5 — Sign-off with CTA" in CAMPAIGN_TEMPLATE_V2

    def test_v1_has_legacy_phrases(self) -> None:
        # v1 is frozen; these phrases must stay so rollback is identical.
        assert "Step 1 — Initial outreach" in CAMPAIGN_TEMPLATE_V1
        assert "Step 2 — Spec CV" in CAMPAIGN_TEMPLATE_V1
        assert "Every variant for a given sequence must convey the SAME core message" in CAMPAIGN_TEMPLATE_V1

    def test_v2_does_not_use_outreach_angle(self) -> None:
        """v2 drops the {outreach_angle} slot; v1 keeps it."""
        assert "{outreach_angle}" not in CAMPAIGN_TEMPLATE_V2
        assert "{outreach_angle}" in CAMPAIGN_TEMPLATE_V1

    def test_v2_has_offer_of_value_rule(self) -> None:
        """Every email must close with a concrete offer of value (v2 rev
        2026-04-20b). Guards against accidental removal of the rule."""
        # Global-rules bullet
        assert "concrete offer of value" in CAMPAIGN_TEMPLATE_V2
        # Anti-pattern the rule explicitly forbids
        assert 'vague "let me know if interested"' in CAMPAIGN_TEMPLATE_V2
        # Variation requirement
        assert "Vary the offer across the five sequences" in CAMPAIGN_TEMPLATE_V2
        # Self-check line at the end
        assert "five offers within each tone-arc are distinct from each other" in CAMPAIGN_TEMPLATE_V2

    def test_v1_does_not_have_offer_of_value_rule(self) -> None:
        """v1 is frozen; the new rule lives only on v2 so rollback is clean."""
        assert "concrete offer of value" not in CAMPAIGN_TEMPLATE_V1
        assert "Vary the offer across the five sequences" not in CAMPAIGN_TEMPLATE_V1


# ── resolve_campaign_prompt selects by flag ───────────────────────

class TestResolverVersionSelection:

    def test_default_is_v2(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier())
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "TONE CONTROLS CONTENT" in user
        assert "Step 1 — Initial outreach" not in user  # v1-only header

    def test_explicit_v2(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Campaign anchors" in user

    def test_explicit_v1(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v1")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Step 1 — Initial outreach" in user
        assert "TONE CONTROLS CONTENT" not in user

    def test_case_insensitive(self) -> None:
        m1 = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="V1")
        u1 = next(m["content"] for m in m1 if m["role"] == "user")
        assert "Step 1 — Initial outreach" in u1

        m2 = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="V2")
        u2 = next(m["content"] for m in m2 if m["role"] == "user")
        assert "TONE CONTROLS CONTENT" in u2

    def test_unknown_version_falls_back_to_v2(self) -> None:
        """Guardrail: any value that isn't 'v1' picks v2 (fail-forward)."""
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="nonsense")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "TONE CONTROLS CONTENT" in user


# ── Placeholder completeness (renders cleanly, no KeyError) ───────

class TestPlaceholderCompleteness:

    def test_v2_renders_without_key_error(self) -> None:
        """v2 must not reference any placeholder that resolve_campaign_prompt doesn't supply."""
        # If this raises, v2 has a placeholder the resolver isn't filling.
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v2")
        assert len(messages) == 2

    def test_v1_renders_without_key_error(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v1")
        assert len(messages) == 2

    def test_v2_substitutes_core_dossier_fields(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        # Job data substituted
        assert "Barclays" in user
        assert "Head of Credit Risk" in user
        assert "London" in user
        # Dossier fields substituted
        assert "Barclays is repositioning" in user
        assert "Senior credit risk leader departed" in user
        # HM line should include the high-confidence HM
        assert "Jane Doe" in user and "Chief Risk Officer" in user

    def test_system_prompt_unchanged(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v2")
        system = next(m["content"] for m in messages if m["role"] == "system")
        assert system == CAMPAIGN_SYSTEM
