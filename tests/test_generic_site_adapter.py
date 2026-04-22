"""Tests for the generic_site adapter's location-extraction helpers.

Focus of this file is the ``_location_from_title`` fallback shipped
with PR #50 after the 2026-04-22 per-adapter audit surfaced ~2K
generic_site failing rows where the location was embedded inside the
concatenated card title.

Fixtures are copied verbatim from
``artifacts/generic_site-failing-2026-04-22.xlsx``. Two patterns
covered:

* **Pattern A — Bupa label/value** (``<title>\\nLocation\\n<value>\\n…``)
* **Pattern B — Goldman middle-dot trilogy** (``<title>\\n<city>·<country>\\n·<level>``)

Also checks:

* Single-line titles are no-ops.
* Titles with ``·`` get the middle-dot normalised to ", " so the
  downstream ``location_normaliser`` resolves them via structured
  parse.
* Pathological inputs (None, empty, just a label with no value) do
  not crash.
"""

from __future__ import annotations

import pytest

from vacancysoft.adapters.generic_browser import _location_from_title
from vacancysoft.enrichers.location_normaliser import normalise_location


# ── Pattern A (Bupa-style): "Location" label line ──────────────────


class TestPatternALabelValue:

    @pytest.mark.parametrize("title,expected", [
        (
            "Business Manager\nLocation\nSalford Quays\nPosition type\nFull Time\nDistance\nFind out more",
            "Salford Quays",
        ),
        (
            "Supplier Relationship Manager - Intra Group Relationships\nLocation\nBupa Place\nPosition type\nFull Time\nDistance\nFind out more",
            "Bupa Place",
        ),
        (
            "Business Manager\nLocation\nStaines\nPosition type\nFull Time\nDistance\nFind out more",
            "Staines",
        ),
        (
            "Actuarial Pricing Analyst\nLocation\nStaines - Willow House\nPosition type\nFull Time\nDistance\nFind out more",
            "Staines - Willow House",
        ),
    ])
    def test_bupa_style_label_value(self, title: str, expected: str) -> None:
        assert _location_from_title(title) == expected

    def test_location_label_case_insensitive(self) -> None:
        assert _location_from_title("Title\nLOCATION\nLondon\nMore") == "London"
        assert _location_from_title("Title\nlocation\nLondon\nMore") == "London"

    def test_label_with_no_value_returns_none(self) -> None:
        # Label at end of string, no following value line.
        assert _location_from_title("Title\nLocation") is None


# ── Pattern B (Goldman-style): middle-dot trilogy ──────────────────


class TestPatternBMiddleDot:

    @pytest.mark.parametrize("title,expected", [
        (
            "Risk - Analytics & Reporting - Vice President - Birmingham\nBirmingham·United Kingdom\n·Vice President",
            "Birmingham, United Kingdom",
        ),
        (
            "Internal Audit, Data Analytics, Technology Audit, Vice President, Dallas\nDallas·United States\n·Vice President",
            "Dallas, United States",
        ),
        (
            "Engineering-L2-Bengaluru-Associate-Security Engineering\nBengaluru·India\n·Associate",
            "Bengaluru, India",
        ),
        (
            "Risk-Hyderabad-Vice President-Software Engineering\nHyderabad·India\n·Vice President",
            "Hyderabad, India",
        ),
        (
            "AMD Public-Salt Lake City-Associate-Software Engineering\nSalt Lake City·United States\n·Associate",
            "Salt Lake City, United States",
        ),
    ])
    def test_goldman_style_middle_dot(self, title: str, expected: str) -> None:
        assert _location_from_title(title) == expected

    def test_goldman_style_without_middle_dot_on_location_line(self) -> None:
        """One common variant has no country on line 2 (just the city).
        The ·-prefixed level line on line 3 is still the signal."""
        title = "Risk, Operational Risk, Analyst, Hong Kong\nHong Kong\n·Associate"
        assert _location_from_title(title) == "Hong Kong"


# ── Integration: fallback output is resolvable by the enricher ─────


class TestEnricherRoundTrip:
    """The fallback output must survive the location_normaliser so it
    actually lands in EnrichedJob.location_* downstream, not just
    RawJob.location_raw."""

    @pytest.mark.parametrize("title,expected_country,expected_city", [
        # Goldman London → UK
        (
            "Risk - Analytics & Reporting - Vice President - Birmingham\nBirmingham·United Kingdom\n·Vice President",
            "UK",
            "Birmingham",
        ),
        # Goldman Dallas → USA
        (
            "Engineering - Dallas\nDallas·United States\n·Analyst",
            "USA",
            "Dallas",
        ),
        # Bupa Staines → UK (bare UK-known-town, resolves via PR #47's _UK_KNOWN_TOWNS)
        (
            "Actuarial Pricing Analyst\nLocation\nStaines\nPosition type\nFull Time",
            "UK",
            "Staines",
        ),
    ])
    def test_fallback_resolves_through_enricher(
        self, title: str, expected_country: str, expected_city: str
    ) -> None:
        extracted = _location_from_title(title)
        assert extracted is not None, f"fallback failed on {title!r}"
        result = normalise_location(extracted)
        assert result.get("country") == expected_country, (
            f"title={title!r} extracted={extracted!r} "
            f"enricher returned country={result.get('country')!r}, "
            f"expected {expected_country!r}"
        )
        assert result.get("city") == expected_city, (
            f"title={title!r} extracted={extracted!r} "
            f"enricher returned city={result.get('city')!r}, "
            f"expected {expected_city!r}"
        )


# ── No-op and edge cases ───────────────────────────────────────────


class TestFallbackEdgeCases:

    def test_single_line_title_is_noop(self) -> None:
        # The ~10K "clean title" failures (BofA, Macquarie, Equifax,
        # Point72) must NOT get a bogus location from this fallback.
        # They fail structurally and need a CSS-selector fix that PR
        # 3B-ii's capture mode will inform.
        assert _location_from_title("Financial Solutions Advisor - Central South NJ Market") is None
        assert _location_from_title("Compliance Analyst - Primary Research") is None
        assert _location_from_title("Software Engineer") is None

    def test_none_returns_none(self) -> None:
        assert _location_from_title(None) is None

    def test_empty_returns_none(self) -> None:
        assert _location_from_title("") is None
        assert _location_from_title("\n\n\n") is None

    def test_two_line_title_with_no_signal_returns_none(self) -> None:
        # A two-line title without a "Location" label or a ·-prefix
        # line is NOT assumed to carry location. Conservative choice —
        # if we misfire here we'd silently corrupt location_raw for
        # titles that happen to have a newline for unrelated reasons.
        assert _location_from_title("Title Line 1\nSome other content") is None

    def test_middle_dot_inside_a_normal_title_is_noop(self) -> None:
        """A single-line title with a middle-dot (e.g. a branded
        separator like 'Team · Role') must not trip pattern B —
        pattern B requires the middle-dot-prefixed line to be on line
        >= 2, not line 1."""
        assert _location_from_title("Engineering · Senior SWE") is None

    def test_location_label_with_trailing_colon(self) -> None:
        """Some sites write 'Location:' with a colon. Pattern A's
        exact-match check rejects this — that's fine; rare variant,
        and extending the match risks false positives (any line
        ending in ':' would match). Covered by _LOCATION_LABEL_PREFIXES
        in _sniff_location instead.
        """
        # Conservative: this variant is NOT caught by the title
        # fallback. We document the behaviour rather than pretending
        # to fix it here.
        assert _location_from_title("Role\nLocation:\nLondon") is None


# ── Documentation for what this PR deliberately does NOT fix ───────


class TestOutOfScope:
    """Placeholder tests documenting the buckets of generic_site
    failures that PR #50 does NOT address, so a future engineer
    reading the test file understands the scope.

    Expected behaviour for each: ``_location_from_title`` returns
    None. The real fix lives in PR 3B-ii (capture mode + selector
    additions) or in dedicated per-ATS adapters.
    """

    def test_bucket_a_wrong_element_scrape_is_not_fixed(self) -> None:
        """UniCredit (src#791) produces title_raw like 'Romanian' /
        'Serbian' — the adapter is clicking on language switcher
        dropdown options, not job cards. Location-parsing can't
        recover this; discovery-level bug."""
        assert _location_from_title("Romanian") is None
        assert _location_from_title("Serbian") is None

    def test_bucket_c_clean_title_with_separate_location_dom_is_not_fixed(self) -> None:
        """Bank of America / Macquarie / Equifax / Point72 etc. —
        clean single-line titles, location lives in a sibling DOM
        node with non-standard CSS classes. Needs PR 3B-ii."""
        assert _location_from_title("Financial Solutions Advisor - Central South NJ Market") is None
