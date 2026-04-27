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

def _job(description: str | None = None) -> dict[str, str]:
    return {
        "title": "Head of Credit Risk",
        "company": "Barclays",
        "location": "London",
        # Default to a short canned body so the reference appendix has
        # something to render in happy-path tests. Tests exercising the
        # truncation / empty / brace-escape paths pass their own.
        "description": "Owns the wholesale credit book across EMEA. Works closely with the CRO. Requires 15 yrs IB experience." if description is None else description,
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

    def test_v2_has_closed_offer_menu(self) -> None:
        """Revised 2026-04-21: closes are constrained to a five-item
        closed list (call / profiles / pen portrait / salary benchmark /
        sense check). The previous open-ended 'any concrete offer of
        value' rule and the 'vary across sequences' rule produced
        inflated offers (market notes, sequencing analyses, briefings)
        the recruiter couldn't credibly deliver."""
        # Closed-list preamble — unique phrase that guards the menu shape
        assert "closed list of five" in CAMPAIGN_TEMPLATE_V2
        # Each of the five menu items appears as a named offer
        assert "A short conversation" in CAMPAIGN_TEMPLATE_V2
        assert "A few relevant profiles" in CAMPAIGN_TEMPLATE_V2
        # Renamed 2026-04-21 from "A single named pen portrait" →
        # "A short candidate overview" to stop the model rendering it
        # as meta "pen picture of candidate patterns we're seeing"
        # language instead of concrete "who can do X" descriptions.
        assert "A short candidate overview" in CAMPAIGN_TEMPLATE_V2
        assert "A single named pen portrait" not in CAMPAIGN_TEMPLATE_V2
        assert "A salary benchmark" in CAMPAIGN_TEMPLATE_V2
        assert "A sense check on the spec" in CAMPAIGN_TEMPLATE_V2
        # Negative guard-rail lists the specific inflations we've seen
        assert '"market notes"' in CAMPAIGN_TEMPLATE_V2
        assert '"sequencing analyses"' in CAMPAIGN_TEMPLATE_V2
        # Anti-pattern the rule still forbids
        assert 'vague "let me know if interested"' in CAMPAIGN_TEMPLATE_V2
        # Level sequences 2-4 rule
        assert "Sequences 2, 3 and 4 carry the same light-touch weight" in CAMPAIGN_TEMPLATE_V2
        # Self-check on the verification list
        assert "five-item closed list" in CAMPAIGN_TEMPLATE_V2

    def test_v2_drops_old_variety_rule(self) -> None:
        """The 'vary the offer across the five sequences' rule is gone
        (removed 2026-04-21). Repetition across sequences is now fine."""
        assert "Vary the offer across the five sequences" not in CAMPAIGN_TEMPLATE_V2
        assert "five offers within each tone-arc are distinct" not in CAMPAIGN_TEMPLATE_V2

    def test_v1_does_not_have_offer_rules(self) -> None:
        """v1 is frozen; neither the old nor new offer rules live there."""
        assert "closed list of five" not in CAMPAIGN_TEMPLATE_V1
        assert "concrete offer of value" not in CAMPAIGN_TEMPLATE_V1
        assert "Vary the offer across the five sequences" not in CAMPAIGN_TEMPLATE_V1

    def test_v2_has_spoken_english_guards(self) -> None:
        """Revised 2026-04-21 after operator smoke flagged three tics:
        stilted self-identification ("I'm with Barclay Simpson"),
        evaluator word choice ("harder to test from inbound CVs"), and
        abstract candidate descriptions ("pen picture of candidate
        patterns we're seeing when firms want X"). All three guards
        apply globally — every tone, every sequence."""
        # Self-identification rule
        assert 'I work for Barclay Simpson' in CAMPAIGN_TEMPLATE_V2
        assert "I'm with Barclay Simpson" in CAMPAIGN_TEMPLATE_V2  # appears in the negative examples
        # Observational vs evaluator language rule
        assert "observational words" in CAMPAIGN_TEMPLATE_V2
        assert '"test"' in CAMPAIGN_TEMPLATE_V2  # appears in the negative list
        assert '"determine"' in CAMPAIGN_TEMPLATE_V2
        # Concrete candidate description rule
        assert "describe what they CAN DO" in CAMPAIGN_TEMPLATE_V2
        assert 'Candidate patterns we\'re seeing when firms want X' in CAMPAIGN_TEMPLATE_V2
        # Verification checklist mirrors
        assert 'no email uses "I\'m with Barclay Simpson"' in CAMPAIGN_TEMPLATE_V2
        assert 'no email uses evaluator words' in CAMPAIGN_TEMPLATE_V2
        assert 'candidate references are concrete' in CAMPAIGN_TEMPLATE_V2

    def test_v1_does_not_have_spoken_english_guards(self) -> None:
        """v1 is frozen; the new guards live only on v2."""
        assert "I work for Barclay Simpson" not in CAMPAIGN_TEMPLATE_V1
        assert "observational words" not in CAMPAIGN_TEMPLATE_V1
        assert "describe what they CAN DO" not in CAMPAIGN_TEMPLATE_V1

    def test_v2_suppresses_stage_leakage(self) -> None:
        """Revised 2026-04-21 after a smoke-test email opened with
        'A recurring mid-stage tension on this sort of brief is…'.
        The sequence-stage labels in the prompt (early-stage /
        mid-stage / late-stage) are internal targeting, not body
        language. The rule + checklist item below explicitly ban
        echoing them — applies to every tone and every sequence."""
        # Dedicated sub-section after the sequence descriptions
        assert "Stage framing is internal" in CAMPAIGN_TEMPLATE_V2
        # Global-rule bullet
        assert "Do not reference the hiring process stage or timeline in the email prose" in CAMPAIGN_TEMPLATE_V2
        # Negative examples the rule names explicitly
        assert '"a recurring mid-stage tension"' in CAMPAIGN_TEMPLATE_V2
        assert '"early-stage pain"' in CAMPAIGN_TEMPLATE_V2
        assert '"by now you\'re probably seeing"' in CAMPAIGN_TEMPLATE_V2
        # Self-check on the verification list
        assert "no email references the hiring process stage or timeline" in CAMPAIGN_TEMPLATE_V2

    def test_v1_does_not_have_stage_leak_guard(self) -> None:
        assert "Stage framing is internal" not in CAMPAIGN_TEMPLATE_V1
        assert "Do not reference the hiring process stage" not in CAMPAIGN_TEMPLATE_V1


# ── resolve_campaign_prompt selects by flag ───────────────────────

class TestResolverVersionSelection:

    def test_default_is_v3(self) -> None:
        """Default flipped to V3 on 2026-04-27 (GPT-5.5 + persona-led)."""
        messages = resolve_campaign_prompt("risk", _job(), _dossier())
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Treat the tone keys as creative briefs" in user  # v3-only marker
        assert "TONE CONTROLS CONTENT" not in user  # v2-only marker
        assert "Step 1 — Initial outreach" not in user  # v1-only header

    def test_explicit_v3(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="v3")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Treat the tone keys as creative briefs" in user
        # category-driven persona block
        assert "risk recruitment specialist at Barclay Simpson" in user

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

        m3 = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="V3")
        u3 = next(m["content"] for m in m3 if m["role"] == "user")
        assert "Treat the tone keys as creative briefs" in u3

    def test_unknown_version_falls_back_to_v3(self) -> None:
        """Guardrail: any value that isn't 'v1' or 'v2' picks v3 (default since 2026-04-27)."""
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), template_version="nonsense")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Treat the tone keys as creative briefs" in user


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


# ── JD-passthrough appendix (v2 only) ─────────────────────────────

class TestDescriptionPassthrough:
    """v2's `{description}` appendix lets any tone ground in advert text.
    Guards truncation (6,000 chars), brace escaping (JDs that contain `{`
    or `}` must not break .format()), the "Not available" fallback, and
    that v1 doesn't accidentally sprout the appendix on a rollback."""

    def test_description_renders_into_v2(self) -> None:
        job = _job("Owns the wholesale credit book across EMEA.")
        messages = resolve_campaign_prompt("risk", job, _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        assert "Owns the wholesale credit book across EMEA." in user
        # Heading should be present so the model knows where the appendix begins.
        assert "Source Job Description" in user

    def test_description_long_body_is_truncated(self) -> None:
        long_body = "X" * 8000
        messages = resolve_campaign_prompt("risk", _job(long_body), _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        # The marker goes in at the resolver's cap; verify it landed.
        assert "truncated" in user
        # And that we actually clipped — a full 8k-char run of Xs would
        # otherwise appear literally. Check a substring longer than the
        # cap doesn't appear.
        assert "X" * 6100 not in user

    def test_description_escapes_curly_braces(self) -> None:
        """JDs with `{foo}` must not crash .format()."""
        body = "Tech stack: Python, {REST APIs}, kdb+. Team uses {agile} rituals."
        # If the resolver didn't escape, the format() call would raise
        # IndexError or KeyError on the unknown placeholder.
        messages = resolve_campaign_prompt("risk", _job(body), _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        # Braces round-trip back to single in the rendered output.
        assert "{REST APIs}" in user
        assert "{agile}" in user

    def test_description_empty_falls_back_to_not_available(self) -> None:
        messages = resolve_campaign_prompt("risk", _job(""), _dossier(), template_version="v2")
        user = next(m["content"] for m in messages if m["role"] == "user")
        # Heading still renders (it's static template text) but the slot
        # reads as "Not available" rather than a blank section.
        assert "Source Job Description" in user
        assert "Not available" in user

    def test_v1_does_not_include_description_appendix(self) -> None:
        """v1 is frozen — rollback must not pick up the new section."""
        assert "Source Job Description" not in CAMPAIGN_TEMPLATE_V1
        assert "{description}" not in CAMPAIGN_TEMPLATE_V1

    def test_v2_advertises_description_in_template(self) -> None:
        """Template must actually reference {description} for passthrough
        to work; guards against an accidental revert of the appendix."""
        assert "{description}" in CAMPAIGN_TEMPLATE_V2
        assert "Source Job Description" in CAMPAIGN_TEMPLATE_V2
