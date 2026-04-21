"""Tests for the resolver's ``user_context`` parameter (voice layer).

Pure-string tests — no DB. Uses ``resolve_campaign_prompt`` directly
to verify:

- cold-start byte-identical output (user_context=None OR empty-shape)
- authored tone prompts render with per-tone bullets, and tones
  without overrides render "(no override — use default)"
- voice samples render as few-shot blocks per sequence, with tone
  tag + subject + body per sample; sequences with zero samples are
  omitted entirely (no empty headers)
- braces in operator-authored text / scraped subjects do NOT crash
  the overall ``template.format()`` call
- v1 (legacy rollback target) never picks up the voice layer — the
  rollback path stays byte-identical
"""

from __future__ import annotations

from vacancysoft.intelligence.prompts.base_campaign import CAMPAIGN_TEMPLATE_V1
from vacancysoft.intelligence.prompts.resolver import (
    _render_voice_layer,
    resolve_campaign_prompt,
)


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


def _empty_user_context() -> dict:
    """A user_context shape with a real user id/email but no authored
    prompts and no voice samples. Cold-start — should render identical
    to user_context=None."""
    _empty_tone_samples = {seq: [] for seq in range(1, 6)}
    return {
        "user_id": "abc",
        "display_name": "Antony B.",
        "email": "ab@barclaysimpson.com",
        "tone_prompts": {
            "formal": "", "informal": "", "consultative": "",
            "direct": "", "candidate_spec": "", "technical": "",
        },
        # Shape: {tone: {sequence_index: [samples...]}}.
        # Strict per-tone-per-sequence windowing (5 per slot).
        "voice_samples_by_tone": {
            tone: dict(_empty_tone_samples) for tone in
            ("formal", "informal", "consultative", "direct", "candidate_spec", "technical")
        },
    }


# ── Cold-start regression guards ───────────────────────────────────


class TestColdStart:

    def test_user_context_none_byte_identical_to_no_voice_layer(self) -> None:
        """user_context=None means the voice_layer slot renders empty
        string → output is byte-identical to pre-voice-layer. Regression
        guard against any accidental always-render path."""
        base = resolve_campaign_prompt("risk", _job(), _dossier())
        user_none = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=None)
        assert base[0]["content"] == user_none[0]["content"]
        assert base[1]["content"] == user_none[1]["content"]
        # Positive check: no Voice-layer heading slipped in
        assert "# Voice layer" not in user_none[1]["content"]

    def test_empty_user_context_also_renders_empty(self) -> None:
        """A user who's been bootstrapped but has NO authored prompts
        and NO voice samples — same cold-start behaviour."""
        base = resolve_campaign_prompt("risk", _job(), _dossier())
        empty = resolve_campaign_prompt(
            "risk", _job(), _dossier(), user_context=_empty_user_context(),
        )
        assert "# Voice layer" not in empty[1]["content"]
        assert base[1]["content"] == empty[1]["content"]


# ── Authored tone prompts ──────────────────────────────────────────


class TestAuthoredTonePrompts:

    def test_single_authored_prompt_renders_voice_layer(self) -> None:
        ctx = _empty_user_context()
        ctx["tone_prompts"]["informal"] = "Keep it short. I use 'cheers' sometimes."
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "# Voice layer for Antony B." in user
        assert "Authored voice guidance" in user
        assert "I use 'cheers' sometimes." in user
        # Unauthored tones render the "(no override — use default)" line
        # so the model knows the slot is intentionally empty.
        assert "**formal**: (no override — use default)" in user

    def test_authored_prompts_survive_brace_escape(self) -> None:
        """Operator text with literal `{` / `}` must not crash
        `template.format()` — brace-escape test. Real trigger: a user
        typing 'I like {short} and {punchy} closes' or pasting a code
        snippet."""
        ctx = _empty_user_context()
        ctx["tone_prompts"]["informal"] = "Use {short} sentences. {punchy} closes."
        # If braces weren't escaped, format() would raise on {short}
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        # Single braces round-trip back (escape is internal to the block's format pass)
        assert "{short}" in user
        assert "{punchy}" in user

    def test_all_six_tones_listed_even_when_only_some_authored(self) -> None:
        ctx = _empty_user_context()
        ctx["tone_prompts"]["informal"] = "X"
        ctx["tone_prompts"]["technical"] = "Y"
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        for tone in ("formal", "informal", "consultative", "direct", "candidate_spec", "technical"):
            assert f"**{tone}**:" in user


# ── Voice samples (strict per-tone grouping) ───────────────────────


class TestVoiceSamples:

    def test_voice_samples_render_grouped_per_tone(self) -> None:
        ctx = _empty_user_context()
        # Two informal samples for seq 1, one formal sample for seq 3.
        ctx["voice_samples_by_tone"]["informal"][1] = [
            {"subject": "A quick thought on the risk role", "body": "Hi. I work for Barclay Simpson. Cheers.", "tone": "informal"},
            {"subject": "Second one", "body": "Short and friendly.", "tone": "informal"},
        ]
        ctx["voice_samples_by_tone"]["formal"][3] = [
            {"subject": "Formal follow-up", "body": "A measured note.", "tone": "formal"},
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        # Per-tone sections, not per-sequence
        assert "### informal — 2 samples on file" in user
        assert "### formal — 1 sample on file" in user
        # Strict-tone-matching preamble appears
        assert "STRICT TONE MATCHING" in user
        # Samples tagged with their sequence
        assert "[seq 1]" in user
        assert "[seq 3]" in user
        # Actual content
        assert "A quick thought on the risk role" in user
        assert "Formal follow-up" in user

    def test_strict_tone_matching_message_present(self) -> None:
        """The prompt must tell the model to imitate ONLY the right-tone
        samples. Without this instruction, the samples from one tone
        can leak into other tones' variants, defeating the purpose of
        per-tone windowing."""
        ctx = _empty_user_context()
        ctx["voice_samples_by_tone"]["informal"][1] = [
            {"subject": "hi", "body": "body", "tone": "informal"},
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "STRICT TONE MATCHING" in user
        assert "Do NOT cross-pollinate patterns between tones" in user

    def test_tones_with_zero_samples_are_omitted(self) -> None:
        """Empty tone buckets must not render empty headers."""
        ctx = _empty_user_context()
        ctx["voice_samples_by_tone"]["direct"][3] = [
            {"subject": "Mid one", "body": "Body mid.", "tone": "direct"},
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "### direct — 1 sample on file" in user
        # No empty tone headings for the other five tones
        for tone in ("formal", "informal", "consultative", "candidate_spec", "technical"):
            assert f"### {tone} —" not in user

    def test_sample_bodies_with_braces_survive(self) -> None:
        """A scraped subject line containing `{}` must not crash format()."""
        ctx = _empty_user_context()
        ctx["voice_samples_by_tone"]["direct"][1] = [
            {"subject": "Re: {placeholder} test", "body": "Body with {code}.", "tone": "direct"},
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "{placeholder}" in user
        assert "{code}" in user

    def test_multiple_sequences_within_one_tone_grouped_under_same_header(
        self,
    ) -> None:
        """When the same tone has samples in several sequences, they
        all appear under that tone's header, tagged with [seq N]."""
        ctx = _empty_user_context()
        ctx["voice_samples_by_tone"]["informal"][1] = [
            {"subject": "intro", "body": "intro body", "tone": "informal"}
        ]
        ctx["voice_samples_by_tone"]["informal"][3] = [
            {"subject": "middle", "body": "middle body", "tone": "informal"}
        ]
        ctx["voice_samples_by_tone"]["informal"][5] = [
            {"subject": "signoff", "body": "signoff body", "tone": "informal"}
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "### informal — 3 samples on file" in user
        # One tone heading, three [seq N] tags
        assert user.count("### informal") == 1
        assert "[seq 1]" in user
        assert "[seq 3]" in user
        assert "[seq 5]" in user


# ── Combined authored + samples ────────────────────────────────────


class TestCombined:

    def test_both_render_when_both_present(self) -> None:
        ctx = _empty_user_context()
        ctx["tone_prompts"]["formal"] = "Always start with the company context."
        ctx["voice_samples_by_tone"]["informal"][2] = [
            {"subject": "Week 2 nudge", "body": "Short body.", "tone": "informal"},
        ]
        messages = resolve_campaign_prompt("risk", _job(), _dossier(), user_context=ctx)
        user = messages[1]["content"]
        assert "Authored voice guidance" in user
        assert "strict per-tone voice samples" in user
        # Voice layer sits between global rules and output schema
        assert user.index("# Voice layer") < user.index("# Output schema")
        # Global rules still come before the voice layer
        assert user.index("# Global rules") < user.index("# Voice layer")


# ── v1 rollback path ───────────────────────────────────────────────


class TestV1FrozenAgainstVoiceLayer:

    def test_v1_template_does_not_contain_voice_layer_slot(self) -> None:
        """Frozen v1 must never reference {voice_layer}."""
        assert "{voice_layer}" not in CAMPAIGN_TEMPLATE_V1
        assert "Voice layer for" not in CAMPAIGN_TEMPLATE_V1

    def test_v1_renders_with_user_context_populated(self) -> None:
        """v1 silently ignores user_context — .format() is permissive
        about extra kwargs. Output must NOT contain the voice layer."""
        ctx = _empty_user_context()
        ctx["tone_prompts"]["informal"] = "Should not appear in v1 output."
        messages = resolve_campaign_prompt(
            "risk", _job(), _dossier(), template_version="v1", user_context=ctx,
        )
        user = messages[1]["content"]
        assert "# Voice layer" not in user
        assert "Should not appear in v1 output." not in user


# ── Unit test for _render_voice_layer ──────────────────────────────


class TestRenderVoiceLayerUnit:

    def test_none_returns_empty(self) -> None:
        assert _render_voice_layer(None) == ""

    def test_empty_shape_returns_empty(self) -> None:
        assert _render_voice_layer(_empty_user_context()) == ""

    def test_unknown_tone_keys_ignored(self) -> None:
        """A payload with an unknown tone key should not break
        rendering — only the known six are surfaced."""
        ctx = _empty_user_context()
        ctx["tone_prompts"]["bogus"] = "should be silently dropped"
        ctx["tone_prompts"]["informal"] = "should render"
        block = _render_voice_layer(ctx)
        assert "should render" in block
        assert "should be silently dropped" not in block
