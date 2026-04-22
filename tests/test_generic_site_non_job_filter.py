"""Tests for the generic_site adapter's ``_looks_like_non_job_title``
filter — Slice A1 additions (2026-04-22).

Context: the 2026-04-22 per-adapter location audit
(``scripts/audit_adapter_locations.py``,
``artifacts/generic_site-failing-2026-04-22.xlsx``) surfaced ~4K
RawJob rows where title_raw isn't a job title at all — the adapter's
wildcard link selectors (``[class*='opportunit'] a``, ``li a``,
``tr a``) were picking up non-job UI elements:

* **UniCredit (src#791, 2,756 rows)** — language-switcher <option> tags
  rendered as anchor text (``"Romanian"``, ``"Serbian"``, ``"Deutsch"``).
* **McDonald's (src#888, 1,009 rows)** — pagination buttons whose
  innerText is just the page number (``"698"``, ``"699"``, ``"700"``).

This PR extends ``_looks_like_non_job_title()`` with two rejection
patterns that catch these at the adapter layer so future scrapes
don't insert the junk in the first place. Existing rows stay in the
DB until the follow-up cleanup script removes them (Step 5 handoff
note).
"""

from __future__ import annotations

import pytest

from vacancysoft.adapters.generic_browser import _looks_like_non_job_title


# ── Bare-integer titles (McDonald's pagination) ────────────────────


class TestBareIntegerTitles:
    """Integer-only strings are never job titles."""

    @pytest.mark.parametrize("title", [
        "698", "699", "700", "701",       # McDonald's pagination buttons
        "1", "2", "10", "99",             # Generic short pagination
        "12345", "999999",                # Multi-digit
        "001",                             # Leading zero
    ])
    def test_bare_integer_rejected(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is True, (
            f"Expected _looks_like_non_job_title({title!r}) to be True"
        )

    def test_whitespace_around_integer_still_rejected(self) -> None:
        # The function strips before checking; trailing/leading spaces
        # on a pure-number title shouldn't slip it past the filter.
        assert _looks_like_non_job_title("  698  ") is True
        assert _looks_like_non_job_title("\t42\n") is True


class TestRealTitlesWithNumbersNotRejected:
    """Titles that contain a number but aren't bare integers must pass
    the filter — we don't want to accidentally reject legitimate job
    titles that include level / grade / requisition numbers."""

    @pytest.mark.parametrize("title", [
        "Analyst II",
        "Software Engineer 3",
        "Director, Level 4",
        "Engineer (Grade 5)",
        "Role 2024/01",
        "Associate - Tier 2",
        "Head of Risk — 2026 Intake",
    ])
    def test_titles_with_embedded_numbers_pass(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is False, (
            f"Expected _looks_like_non_job_title({title!r}) to be False"
        )


# ── Single-word language names (UniCredit dropdown) ────────────────


class TestLanguageNames:
    """Single-word language names in native form get rejected — these
    are dropdown options from a language switcher, not jobs."""

    @pytest.mark.parametrize("title", [
        "English", "Deutsch", "Italiano",
        "Français", "Francais",                 # accented + ASCII variants
        "Español", "Espanol",
        "Português", "Portugues",
        "Polski",
        "Română", "Romana", "Romanian",
        "Slovenian", "Slovene",
        "Croatian", "Hungarian", "Czech",
        "Turkish", "Russian", "Greek",
    ])
    def test_language_name_rejected(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is True, (
            f"Expected _looks_like_non_job_title({title!r}) to be True"
        )

    @pytest.mark.parametrize("title", [
        "english", "DEUTSCH", "Italiano", "iTaLiAnO",
    ])
    def test_case_insensitive(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is True

    @pytest.mark.parametrize("title", [
        # Native script forms — should match because we lowered both
        # sides and the needles are in the set as-is.
        "српски", "čeština", "türkçe",
    ])
    def test_native_script_language_names(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is True


class TestLegitimateLinguistRoles:
    """Full role titles that happen to mention a language must NOT
    trip the filter — the rejection is for BARE single-word language
    names only."""

    @pytest.mark.parametrize("title", [
        "French Translator",
        "Senior German-speaking Analyst",
        "Russian Interpreter",
        "Italian Content Editor",
        "Portuguese Speaking Sales Executive",
        "Spanish Team Lead",
        "Greek-speaking Project Manager",
    ])
    def test_multi_word_linguist_roles_pass(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is False, (
            f"Expected _looks_like_non_job_title({title!r}) to be False"
        )

    def test_english_prefixed_titles_already_rejected_pre_existing(self) -> None:
        """Pre-existing behaviour quirk: "english" is a NON_JOB_TITLE_PREFIX
        (line 215 of generic_browser.py), so any title starting with
        "English" is rejected by the prefix rule — NOT by Slice A1's
        _LANGUAGE_NAMES addition (which only matches bare single-word
        "English"). Documented as a known limitation rather than fixed
        here because (a) the English-prefix rejection predates this PR
        and (b) "English ..." job titles are ~zero in our target
        financial-services markets."""
        # Both are rejected by the pre-existing prefix rule:
        assert _looks_like_non_job_title("English Tutor") is True
        assert _looks_like_non_job_title("English Translator") is True


# ── Regression guards: existing behaviour preserved ────────────────


class TestExistingBehaviourPreserved:
    """Slice A1 is additive — every pre-existing rejection path must
    still fire. Tests the existing signals the filter already checked
    before this PR."""

    def test_empty_string_rejected(self) -> None:
        assert _looks_like_non_job_title("") is True

    def test_whitespace_only_rejected(self) -> None:
        assert _looks_like_non_job_title("   ") is True

    def test_short_codes_rejected(self) -> None:
        # "de", "fr", "en" — already in _NON_JOB_EXACT_TITLES
        assert _looks_like_non_job_title("de") is True
        assert _looks_like_non_job_title("fr") is True

    def test_length_under_3_rejected(self) -> None:
        assert _looks_like_non_job_title("hi") is True

    def test_category_link_rejected(self) -> None:
        # Existing _CATEGORY_LINK_RE path — e.g. "IT ENGINEERING (13)"
        assert _looks_like_non_job_title("IT ENGINEERING (13)") is True
        assert _looks_like_non_job_title("NEW JOBS (558)") is True

    def test_non_job_prefix_rejected(self) -> None:
        # Existing NON_JOB_TITLE_PREFIXES path — e.g. "About Us"
        assert _looks_like_non_job_title("about us") is True
        assert _looks_like_non_job_title("Careers portal") is True

    @pytest.mark.parametrize("title", [
        "Head of Credit Risk",
        "Senior Software Engineer",
        "Vice President, Operational Risk",
        "Director of Analytics",
        "Financial Solutions Advisor - Central South NJ Market",
    ])
    def test_real_job_titles_pass(self, title: str) -> None:
        assert _looks_like_non_job_title(title) is False, (
            f"Real job title {title!r} wrongly flagged as non-job"
        )
