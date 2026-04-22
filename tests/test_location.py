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


# ---------------------------------------------------------------------------
# 2026-04-22 audit-driven fixes (fixes 1, 2, 4, 5)
# ---------------------------------------------------------------------------


class TestGermanKreisPattern:
    """Adzuna German listings frequently use '(Kreis)' as a county marker
    ('Heidenheim (Kreis), Baden-Württemberg'). Resolve to Germany."""

    @pytest.mark.parametrize("raw,expected_city", [
        ("Heidenheim (Kreis), Baden-Württemberg", "Heidenheim"),
        ("Walsrode, Soltau-Fallingbostel (Kreis)", "Walsrode"),
        ("Ravensburg (Kreis)", "Ravensburg"),
        ("Biberach (Kreis), Baden-Württemberg", "Biberach"),
    ])
    def test_kreis_resolves_to_germany(self, raw: str, expected_city: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == "Germany", (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected 'Germany'"
        )
        assert result.get("city") == expected_city, (
            f"normalise_location({raw!r}) -> city={result.get('city')!r}, expected {expected_city!r}"
        )

    def test_kreis_has_medium_high_confidence(self) -> None:
        result = normalise_location("Heidenheim (Kreis)")
        assert result.get("confidence", 0) >= 0.8


class TestUKKnownTownFallback:
    """Reed / Adzuna / direct-ATS feeds often give a bare UK town name
    with no country marker. These must resolve to UK via the curated
    known-town set rather than falling through to country=None."""

    @pytest.mark.parametrize("raw", [
        "Heywood",
        "Egham",
        "Lisvane",
        "Hampstead",
        "Stepney",
        "Hillingdon",
        "Merton",
        "Dagenham",
    ])
    def test_bare_uk_town_resolves_to_uk(self, raw: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == "UK", (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected 'UK'"
        )
        # City should be the town itself, not mangled.
        assert result.get("city") is not None

    def test_case_insensitive(self) -> None:
        assert normalise_location("egham").get("country") == "UK"
        assert normalise_location("EGHAM").get("country") == "UK"
        assert normalise_location("Egham").get("country") == "UK"

    def test_leading_comma_variant_still_resolves(self) -> None:
        """'Heywood, Lancashire' should already resolve via _UK_COUNTIES;
        verify the known-town fallback doesn't break this path."""
        result = normalise_location("Heywood, Lancashire")
        assert result.get("country") == "UK"
        assert result.get("city") == "Heywood"


class TestMultiSiteSentinel:
    """Postings that span multiple locations ('Multiple', 'All Locations',
    '+9More Locations') should resolve to city='Multiple', country=None,
    so the lead is visibly multi-site in reports rather than
    indistinguishable from a parse failure — and is_allowed_country(None)
    keeps the row from being geo_filtered."""

    @pytest.mark.parametrize("raw", [
        "Multiple",
        "Multiple Locations",
        "All Locations",
        "Various",
        "Flexible",
        "Nationwide",
        "UK Wide",
        "@one Sites",
        "+9 More Locations",
        "+1 more",
        "and 2 more",
        "Multi-site",
        "Cross Site",
    ])
    def test_multi_site_resolves_to_multiple(self, raw: str) -> None:
        result = normalise_location(raw)
        assert result.get("city") == "Multiple", (
            f"normalise_location({raw!r}) -> city={result.get('city')!r}, expected 'Multiple'"
        )
        assert result.get("country") is None, (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected None"
        )

    def test_multi_site_is_not_geo_filtered(self) -> None:
        """country=None means is_allowed_country returns True —
        these leads must survive the geo-filter."""
        result = normalise_location("Multiple")
        assert is_allowed_country(result.get("country")) is True

    def test_regular_location_not_mistaken_for_multi_site(self) -> None:
        """'Multiple, UK' has a country context; resolve as UK, not as a
        bare multi-site sentinel."""
        result = normalise_location("Multiple, UK")
        assert result.get("country") == "UK"


class TestTriPartWithISOCountryCode:
    """SmartRecruiters / Workday tri-part format ('Pasay City, PHILIPPINES, ph')
    should resolve to the correct country via the ISO-2 trailing token."""

    @pytest.mark.parametrize("raw,expected_country,expected_city", [
        ("Pasay City, PHILIPPINES, ph", "Philippines", "Pasay City"),
        ("Jakarta, INDONESIA, id", "Indonesia", "Jakarta"),
        ("Abidjan, CÔTE D'IVOIRE, ci", "Côte d'Ivoire", "Abidjan"),
        ("Dakar, SENEGAL, sn", "Senegal", "Dakar"),
        ("Lagos, NIGERIA, ng", "Nigeria", "Lagos"),
        ("Cairo, EGYPT, eg", "Egypt", "Cairo"),
        ("Karachi, PAKISTAN, pk", "Pakistan", "Karachi"),
        ("Ho Chi Minh City, VIETNAM, vn", "Vietnam", "Ho Chi Minh City"),
    ])
    def test_tri_part_iso_code_resolves(
        self, raw: str, expected_country: str, expected_city: str
    ) -> None:
        result = normalise_location(raw)
        assert result.get("country") == expected_country, (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected {expected_country!r}"
        )
        assert result.get("city") == expected_city, (
            f"normalise_location({raw!r}) -> city={result.get('city')!r}, expected {expected_city!r}"
        )

    def test_out_of_region_still_geo_filtered(self) -> None:
        """Philippines is resolved (not None) but still falls outside the
        allowed-country set so the lead is geo_filtered correctly."""
        result = normalise_location("Pasay City, PHILIPPINES, ph")
        assert result.get("country") == "Philippines"
        assert is_allowed_country(result.get("country")) is False


class TestNativeLanguageCountryNames:
    """Native-language country names ('Deutschland', 'Italia', 'Polska')
    should resolve to their English canonical form."""

    @pytest.mark.parametrize("raw,expected_country", [
        ("Deutschland", "Germany"),
        ("Italia", "Italy"),
        ("Polska", "Poland"),
        ("España", "Spain"),
        ("Sverige", "Sweden"),
        ("Norge", "Norway"),
        ("Danmark", "Denmark"),
        ("Brasil", "Brazil"),
        ("México", "Mexico"),
    ])
    def test_native_country_names_resolve(self, raw: str, expected_country: str) -> None:
        result = normalise_location(raw)
        assert result.get("country") == expected_country, (
            f"normalise_location({raw!r}) -> country={result.get('country')!r}, expected {expected_country!r}"
        )
