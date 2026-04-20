"""Tests for the v2 hiring-manager search template.

Covers:
  - `render_hm_search_template_v2` variable substitution
  - Optional location handling (stripped cleanly when empty)
  - `_resolve_hm_searches` flag-driven v1 vs v2 selection in dossier
  - `_resolve_hm_searches` v2-with-empty-sub_specialism fallback to v1
  - hm_search_serpapi's v1/v2 branch path produces parseable queries
"""

from __future__ import annotations

from vacancysoft.intelligence.dossier import _resolve_hm_searches
from vacancysoft.intelligence.prompts.category_blocks import (
    CATEGORY_BLOCKS,
    _HM_SEARCHES_V2_TEMPLATE,
    _LEGACY_RISK_HM_SEARCHES,
    render_hm_search_template_v2,
)


class TestRenderV2Template:
    """The v2 renderer fills [company name] / [function] / [location]."""

    def test_basic_substitution_with_location(self) -> None:
        out = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="Barclays",
            function="Credit Risk",
            location="London",
        )
        # Company is substituted everywhere
        assert '"[company name]"' not in out
        assert '"Barclays"' in out
        # Function is substituted everywhere
        assert "[function]" not in out
        assert "head of Credit Risk" in out
        assert "Credit Risk director" in out
        assert "chief Credit Risk officer" in out
        # Location is substituted and quoted
        assert '"London"' in out
        assert "[location]" not in out
        # Every line should still carry the site: operator
        for line in out.splitlines():
            assert "site:linkedin.com/in" in line

    def test_location_empty_is_stripped_cleanly(self) -> None:
        """Empty location must not leave a trailing ' ""' or ' [location]'."""
        out = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="HSBC",
            function="Financial Crime",
            location="",
        )
        assert "[location]" not in out
        # No dangling empty-quoted term (would kill Google recall)
        assert '""' not in out
        # Each line should end at site:linkedin.com/in with no trailing junk
        for line in out.splitlines():
            assert line.rstrip().endswith("site:linkedin.com/in")

    def test_location_none_is_stripped_cleanly(self) -> None:
        out = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="HSBC",
            function="Financial Crime",
            location=None,
        )
        assert "[location]" not in out
        assert '""' not in out

    def test_whitespace_only_inputs_strip(self) -> None:
        out = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="  Barclays  ",
            function="  Credit Risk  ",
            location="  ",
        )
        assert '"Barclays"' in out
        assert "head of Credit Risk" in out
        assert '""' not in out

    def test_seven_searches_emitted(self) -> None:
        out = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="Barclays",
            function="Credit Risk",
            location="London",
        )
        # Template must emit exactly 7 Search lines (per operator spec)
        search_lines = [
            line for line in out.splitlines() if line.strip().startswith("Search ")
        ]
        assert len(search_lines) == 7


class TestCategoryBlocksWiring:
    """v1 / v2 / legacy-alias keys must be present on every category."""

    def test_every_category_has_both_versions(self) -> None:
        for category, blocks in CATEGORY_BLOCKS.items():
            assert "hm_search_queries_v1" in blocks, f"{category} missing v1"
            assert "hm_search_queries_v2" in blocks, f"{category} missing v2"
            # Back-compat alias must still exist
            assert "hm_search_queries" in blocks, f"{category} missing alias"

    def test_v1_alias_points_at_legacy(self) -> None:
        """The back-compat ``hm_search_queries`` key must still point at v1."""
        assert (
            CATEGORY_BLOCKS["risk"]["hm_search_queries"]
            == CATEGORY_BLOCKS["risk"]["hm_search_queries_v1"]
            == _LEGACY_RISK_HM_SEARCHES
        )


class TestResolveHmSearches:
    """_resolve_hm_searches picks the correct template per flag + input."""

    def _job(self, company: str = "Barclays", location: str = "London") -> dict[str, str]:
        return {"company": company, "location": location}

    def test_v2_with_sub_specialism_renders_template(self) -> None:
        out = _resolve_hm_searches(
            job_data=self._job(),
            category="risk",
            sub_specialism="Credit Risk",
            template_version="v2",
        )
        # v2 renders — company and function should both be substituted
        assert "[company name]" not in out
        assert "[function]" not in out
        assert '"Barclays"' in out
        assert "head of Credit Risk" in out

    def test_v2_without_sub_specialism_falls_back_to_v1(self) -> None:
        """Empty sub_specialism must fall back to v1 (not emit empty [function])."""
        out = _resolve_hm_searches(
            job_data=self._job(),
            category="risk",
            sub_specialism=None,
            template_version="v2",
        )
        # v1 template has "head of credit" / "head of risk" hard-coded
        assert "head of credit" in out.lower()
        # And does NOT contain the v2-only "director of [function]" pattern
        assert "[function]" in out or "director of" not in out.lower() or \
            "director of credit" not in out.lower()
        # Also: v1 still has the literal [company name] placeholder because
        # _resolve_hm_searches only renders v2; v1 is handed raw to serpapi
        # for downstream _substitute.
        assert "[company name]" in out

    def test_v1_returns_legacy_block_unrendered(self) -> None:
        out = _resolve_hm_searches(
            job_data=self._job(),
            category="risk",
            sub_specialism="Credit Risk",  # ignored when v1 selected
            template_version="v1",
        )
        # v1 is returned raw (company-placeholder style for downstream
        # _substitute / LLM interpolation).
        assert "[company name]" in out
        assert "head of credit" in out.lower()

    def test_unknown_category_falls_back_to_default(self) -> None:
        """Unknown categories must not KeyError — they fall to the default."""
        out = _resolve_hm_searches(
            job_data=self._job(),
            category="nonsense-category",
            sub_specialism="Credit Risk",
            template_version="v2",
        )
        # Default category is 'risk', so Credit Risk renders fine
        assert "head of Credit Risk" in out


class TestSerpApiQueryParsing:
    """Verify the v2-rendered template still parses cleanly for SerpApi."""

    def test_parsed_queries_are_clean(self) -> None:
        from vacancysoft.intelligence.hm_search_serpapi import _parse_search_queries

        rendered = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="Barclays",
            function="Credit Risk",
            location="London",
        )
        queries = _parse_search_queries(rendered)
        assert len(queries) == 7
        for q in queries:
            assert "site:linkedin.com/in" in q
            assert "[company name]" not in q
            assert "[function]" not in q
            assert "[location]" not in q
            assert '"Barclays"' in q

    def test_parsed_queries_without_location(self) -> None:
        from vacancysoft.intelligence.hm_search_serpapi import _parse_search_queries

        rendered = render_hm_search_template_v2(
            template=_HM_SEARCHES_V2_TEMPLATE,
            company_name="Barclays",
            function="Credit Risk",
            location=None,
        )
        queries = _parse_search_queries(rendered)
        for q in queries:
            # No dangling empty quote
            assert not q.strip().endswith('""')
            assert q.strip().endswith("site:linkedin.com/in")
