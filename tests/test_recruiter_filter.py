"""Tests for the recruiter exclusion filter."""

from __future__ import annotations

import pytest

from vacancysoft.enrichers.recruiter_filter import is_recruiter


class TestIsRecruiter:
    """is_recruiter should catch agencies by name or keyword."""

    # --- Exact name matches ---
    @pytest.mark.parametrize("name", [
        "Robert Walters",
        "Harnham",
        "Michael Page",
        "Reed",
        "Page Personnel",
        "Oliver James",
        "Taylor Root",
        "Hydrogen Group",
        "Huntress",
        "McGregor Boyall",
    ])
    def test_known_agencies(self, name: str) -> None:
        assert is_recruiter(name) is True

    # --- Keyword fallback ---
    @pytest.mark.parametrize("name", [
        "Acme Recruitment Ltd",
        "Global Staffing Solutions",
        "Alpha Executive Search Partners",
        "Talent Acquisition Services Inc",
        "Premier Headhunters",
    ])
    def test_keyword_catch(self, name: str) -> None:
        assert is_recruiter(name) is True

    # --- Real employers should NOT be flagged ---
    @pytest.mark.parametrize("name", [
        "Goldman Sachs",
        "JPMorgan Chase",
        "BlackRock",
        "Barclays",
        "HSBC",
        "Lloyds Banking Group",
        "Aviva",
        "Deutsche Bank",
        "Morgan Stanley",
        "Citi",
        "AXA",
        "Zurich Insurance",
        "Bank of America",
    ])
    def test_real_employers_not_flagged(self, name: str) -> None:
        assert is_recruiter(name) is False, f"'{name}' should NOT be flagged as recruiter"

    def test_none(self) -> None:
        assert is_recruiter(None) is False

    def test_empty(self) -> None:
        assert is_recruiter("") is False

    def test_case_insensitive(self) -> None:
        assert is_recruiter("ROBERT WALTERS") is True
        assert is_recruiter("robert walters") is True

    def test_partial_name_still_catches_keyword(self) -> None:
        # "Robert Walters UK" isn't an exact name match, but is_recruiter
        # normalises to lowercase and the keyword check is substring-based,
        # so this correctly catches it (no false negative).
        assert is_recruiter("Robert Walters UK") is True


class TestRuntimeTokenSubsetMatch:
    """Token-subset matcher (2026-04-30): runtime YAML entries should catch
    decorated variants of the same company. Without this rule, marking
    'Korn Ferry' as agency would not catch a subsequent 'Korn Ferry
    International' arrival, which is the foot-gun the operator hit."""

    def setup_method(self) -> None:
        from vacancysoft.enrichers import recruiter_filter as rf
        self._original = rf._RUNTIME_EXCLUSIONS.copy()
        # Inject test entries directly so we don't touch the YAML file.
        rf._RUNTIME_EXCLUSIONS = {
            "korn ferry",
            "fincroft",
            "mccabe & barton",
            "people first",
        }

    def teardown_method(self) -> None:
        from vacancysoft.enrichers import recruiter_filter as rf
        rf._RUNTIME_EXCLUSIONS = self._original

    @pytest.mark.parametrize("name", [
        "Korn Ferry International",
        "Korn Ferry UK Ltd",
        "Korn Ferry (Germany) GmbH",
        "Fincroft Bridges",
        "McCabe Barton Recruiting",  # punctuation '&' dropped by tokeniser
        "People First UK",
    ])
    def test_decorated_variants_caught(self, name: str) -> None:
        from vacancysoft.enrichers.recruiter_filter import is_recruiter
        assert is_recruiter(name) is True

    @pytest.mark.parametrize("name", [
        "Goldman Sachs",
        "Look Insurance Brokers",  # 'Look Ahead' tokens are NOT a subset
        "Ferrymead Industries",     # 'ferry' alone won't match 'korn ferry'
    ])
    def test_unrelated_companies_not_caught(self, name: str) -> None:
        from vacancysoft.enrichers.recruiter_filter import is_recruiter
        assert is_recruiter(name) is False
