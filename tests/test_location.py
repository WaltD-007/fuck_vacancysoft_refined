"""Tests for the location normaliser."""

from __future__ import annotations

import pytest

from vacancysoft.enrichers.location_normaliser import normalise_location, is_allowed_country


# ---------------------------------------------------------------------------
# normalise_location — basic country detection
# ---------------------------------------------------------------------------

class TestNormaliseLocation:
    """normalise_location should map raw location strings to {city, country, region, confidence}."""

    @pytest.mark.parametrize("raw,expected_country", [
        ("London", "UK"),
        ("London, UK", "UK"),
        ("London, United Kingdom", "UK"),
        ("Manchester", "UK"),
        ("Edinburgh", "UK"),
        ("New York", "USA"),
        ("New York, NY", "USA"),
        ("Chicago, IL", "USA"),
        ("Toronto", "Canada"),
        ("Toronto, Ontario", "Canada"),
        ("Frankfurt", "Germany"),
        ("Paris", "France"),
        ("Amsterdam", "Netherlands"),
        ("Dublin", "Ireland"),
        ("Zurich", "Switzerland"),
        ("Singapore", "Singapore"),
        ("Hong Kong", "Hong Kong"),
    ])
    def test_country_detection(self, raw: str, expected_country: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == expected_country, (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected {expected_country!r}"
        )

    @pytest.mark.parametrize("raw,expected_city", [
        ("London, UK", "London"),
        ("New York, NY, USA", "New York"),
        ("Manchester, United Kingdom", "Manchester"),
    ])
    def test_city_extraction(self, raw: str, expected_city: str) -> None:
        result = normalise_location(raw)
        assert result.get("city") == expected_city, (
            f"normalise_location({raw!r}) -> city={result.get('city')!r}, expected {expected_city!r}"
        )

    def test_none_input(self) -> None:
        result = normalise_location(None)
        assert result.get("country") is None
        assert result.get("city") is None

    def test_empty_input(self) -> None:
        result = normalise_location("")
        assert result.get("country") is None

    def test_remote_only(self) -> None:
        result = normalise_location("Remote")
        assert isinstance(result, dict)

    def test_confidence_is_set(self) -> None:
        result = normalise_location("London")
        assert "confidence" in result
        assert isinstance(result["confidence"], (int, float))


# ---------------------------------------------------------------------------
# Ambiguous cities — the key fix
# ---------------------------------------------------------------------------

class TestAmbiguousCities:
    """Cities that exist in multiple countries should resolve correctly with context."""

    @pytest.mark.parametrize("raw,expected_country", [
        # With country context — should resolve to the stated country
        ("Birmingham, Alabama, USA", "USA"),
        ("Birmingham, AL", "USA"),
        ("Birmingham, United States", "USA"),
        ("Birmingham, UK", "UK"),
        ("Birmingham, United Kingdom", "UK"),
        ("Cambridge, MA", "USA"),
        ("Cambridge, Massachusetts", "USA"),
        ("Cambridge, UK", "UK"),
        ("Cambridge, United Kingdom", "UK"),
        ("London, Ontario, Canada", "Canada"),
        ("London, ON", "Canada"),
        ("London, UK", "UK"),
        ("Richmond, VA", "USA"),
        ("Richmond, Virginia", "USA"),
        ("Portland, OR", "USA"),
        ("Portland, Oregon", "USA"),
        ("Perth, Australia", "Australia"),
        ("Perth, AU", "Australia"),
        ("Hamilton, Ontario", "Canada"),
        ("Hamilton, Canada", "Canada"),
        ("Windsor, Ontario", "Canada"),
        ("Kingston, Jamaica", None),  # Not in our rules — should be unresolved or Jamaica
        ("Durham, NC", "USA"),
        ("Durham, North Carolina", "USA"),
    ])
    def test_ambiguous_with_context(self, raw: str, expected_country: str | None) -> None:
        result = normalise_location(raw)
        if expected_country is not None:
            assert result.get("country") == expected_country, (
                f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected {expected_country!r}"
            )

    @pytest.mark.parametrize("raw,expected_city", [
        ("Birmingham, AL", "Birmingham"),
        ("Cambridge, MA", "Cambridge"),
        ("London, Ontario, Canada", "London"),
        ("Richmond, VA", "Richmond"),
    ])
    def test_ambiguous_city_name(self, raw: str, expected_city: str) -> None:
        result = normalise_location(raw)
        assert result.get("city") == expected_city, (
            f"normalise_location({raw!r}) -> city={result.get('city')!r}, expected {expected_city!r}"
        )

    def test_bare_ambiguous_defaults_to_primary(self) -> None:
        """Bare 'Birmingham' with no context should still resolve (to UK as primary market)."""
        result = normalise_location("Birmingham")
        assert result.get("country") is not None
        assert result.get("city") == "Birmingham"

    def test_bare_london_is_uk(self) -> None:
        """Bare 'London' should default to UK."""
        result = normalise_location("London")
        assert result.get("country") == "UK"
        assert result.get("city") == "London"

    def test_bare_cambridge_is_uk(self) -> None:
        """Bare 'Cambridge' should default to UK (primary market)."""
        result = normalise_location("Cambridge")
        assert result.get("country") == "UK"


# ---------------------------------------------------------------------------
# Structured parse — comma-separated
# ---------------------------------------------------------------------------

class TestStructuredParse:
    """Comma-separated locations should resolve country from trailing part."""

    @pytest.mark.parametrize("raw,expected_country,expected_city", [
        ("Dallas, TX", "USA", "Dallas"),
        ("Dallas, Texas", "USA", "Dallas"),
        ("Warsaw, Poland", "Poland", "Warsaw"),
        ("Toronto, Ontario", "Canada", "Toronto"),
        ("Milan, Italy", "Italy", "Milan"),
        ("Berlin, DE", "Germany", "Berlin"),
        ("Zurich, CH", "Switzerland", "Zurich"),
        ("Sydney, AU", "Australia", "Sydney"),
        ("Mumbai, IN", "India", "Mumbai"),
    ])
    def test_comma_separated(self, raw: str, expected_country: str, expected_city: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == expected_country
        assert result.get("city") == expected_city

    @pytest.mark.parametrize("raw,expected_country", [
        ("New York, NY, US", "USA"),
        ("Charlotte, NC, USA", "USA"),
        ("San Francisco, CA, United States", "USA"),
    ])
    def test_three_part_usa(self, raw: str, expected_country: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == expected_country

    def test_structured_has_high_confidence(self) -> None:
        result = normalise_location("Birmingham, AL")
        assert result.get("confidence", 0) >= 0.85


# ---------------------------------------------------------------------------
# is_allowed_country
# ---------------------------------------------------------------------------

class TestIsAllowedCountry:
    """is_allowed_country should accept countries in the allowed list."""

    @pytest.mark.parametrize("country", [
        "UK", "USA", "Canada", "Germany", "France", "Netherlands",
        "Switzerland", "Luxembourg", "Hong Kong", "Singapore",
        "UAE", "Saudi Arabia",
    ])
    def test_allowed(self, country: str) -> None:
        assert is_allowed_country(country) is True

    @pytest.mark.parametrize("country", [
        "Brazil", "China", "India", "Russia", "Japan",
        "South Africa", "Mexico", "Thailand", "Australia",
        "Ireland", "Belgium", "Spain", "Italy", "Sweden",
    ])
    def test_not_allowed(self, country: str) -> None:
        assert is_allowed_country(country) is False

    def test_none(self) -> None:
        result = is_allowed_country(None)
        assert isinstance(result, bool)
