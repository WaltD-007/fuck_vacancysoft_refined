"""Tests for the classification pipeline: title rules and taxonomy."""

from __future__ import annotations

import pytest

from vacancysoft.classifiers.taxonomy import classify_against_legacy_taxonomy
from vacancysoft.classifiers.title_rules import is_relevant_title, title_relevance


# ---------------------------------------------------------------------------
# title_relevance scoring
# ---------------------------------------------------------------------------

class TestTitleRelevance:
    """title_relevance() should return 0.95 for high-phrase, 0.80 for medium, 0.15 for miss."""

    @pytest.mark.parametrize("title", [
        "Senior Credit Risk Analyst",
        "Head of Market Risk",
        "Chief Compliance Officer",
        "Internal Audit Manager",
        "Quantitative Researcher - Equities",
        "Cyber Security Engineer",
        "Legal Counsel - Derivatives",
        "Portfolio Manager - Fixed Income",
        "Model Validation Analyst",
        "AML Compliance Officer",
        "Liquidity Risk Manager",
        "Operational Risk Director",
        "Stress Testing Analyst",
        "Treasury Manager",
    ])
    def test_high_relevance_phrases(self, title: str) -> None:
        assert title_relevance(title) == 0.95

    @pytest.mark.parametrize("title", [
        "Risk Consultant",
        "Quant Developer",
        "Compliance Specialist",
        "Audit Associate",
        "Cyber Analyst",
        "Legal Advisor",
        "Trader - Emerging Markets",
        "Financial Analyst",
        # "Actuary" / "Underwriter" omitted — blocklisted in taxonomy._TITLE_BLOCKLIST
        # (insurance actuarial & generic underwriting are not target markets).
    ])
    def test_medium_relevance_words(self, title: str) -> None:
        score = title_relevance(title)
        assert score >= 0.80, f"Expected >= 0.80 for '{title}', got {score}"

    @pytest.mark.parametrize("title", [
        "Software Engineer",
        "HR Business Partner",
        "Marketing Manager",
        "Receptionist",
        "Facilities Coordinator",
        "Social Media Manager",
        "Office Administrator",
    ])
    def test_irrelevant_titles(self, title: str) -> None:
        assert title_relevance(title) == 0.15

    def test_none_title(self) -> None:
        assert title_relevance(None) == 0.0

    def test_empty_title(self) -> None:
        assert title_relevance("") == 0.0


# ---------------------------------------------------------------------------
# is_relevant_title gate
# ---------------------------------------------------------------------------

class TestIsRelevantTitle:
    """is_relevant_title() should gate out non-target titles."""

    @pytest.mark.parametrize("title", [
        "Senior Risk Analyst",
        "VP Credit Risk",
        "Quant Developer",
        "Compliance Manager",
        "Internal Auditor",
        "Cyber Security Architect",
        "Solicitor - Banking",
        "FX Trader",
        pytest.param(
            "Pricing Actuary",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "Actuaries are blocklisted in taxonomy._TITLE_BLOCKLIST "
                    "(see comment at line 45 — insurance actuarial is not a "
                    "target market). This case needs resolving per TODO ticket "
                    "6: either the blocklist should be loosened to admit "
                    "pricing actuary roles, or this assertion should flip to "
                    "is_relevant_title(...) is False."
                ),
            ),
        ),
    ])
    def test_relevant(self, title: str) -> None:
        assert is_relevant_title(title) is True

    @pytest.mark.parametrize("title", [
        "Graphic Designer",
        "Chef de Cuisine",
        "Plumber",
        "Nursery Nurse",
        "Delivery Driver",
    ])
    def test_not_relevant(self, title: str) -> None:
        assert is_relevant_title(title) is False

    def test_none(self) -> None:
        assert is_relevant_title(None) is False

    def test_empty(self) -> None:
        assert is_relevant_title("") is False


# ---------------------------------------------------------------------------
# Sub-specialism routing — 2026-04-30 pass
# ---------------------------------------------------------------------------

class TestRiskSubSpecialismRouting:
    """Risk sub-specialism routing rules added 2026-04-30: pull model /
    modelling / risk-models titles into Quant Risk; pull risk-assurance and
    risk-and-control titles into Operational Risk; pull cross-asset
    derivatives into Market Risk; drop fire / flood-risk titles entirely."""

    @pytest.mark.parametrize("title", [
        "Risk Models Analyst",
        "Senior Risk Modelling Specialist",
        "Risk Modeling Lead",  # US spelling
    ])
    def test_risk_models_routes_to_quant_risk(self, title: str) -> None:
        m = classify_against_legacy_taxonomy(title)
        assert m.primary_taxonomy_key == "risk"
        assert m.sub_specialism == "Quant Risk"

    @pytest.mark.parametrize("title", [
        "Risk Assurance Manager",
        "Head of Risk Assurance",
        "Risk & Control Officer",
        "Risk and Control Lead",
    ])
    def test_risk_assurance_and_control_route_to_operational_risk(self, title: str) -> None:
        m = classify_against_legacy_taxonomy(title)
        assert m.primary_taxonomy_key == "risk"
        assert m.sub_specialism == "Operational Risk"

    @pytest.mark.parametrize("title", [
        "Cross Asset Derivatives Analyst",
        "Cross-Asset Derivatives Trader Risk",
    ])
    def test_cross_asset_derivatives_routes_to_market_risk(self, title: str) -> None:
        m = classify_against_legacy_taxonomy(title)
        assert m.primary_taxonomy_key == "risk"
        assert m.sub_specialism == "Market Risk"

    @pytest.mark.parametrize("title", [
        "Fire Risk Assessor",
        "Flood Risk Engineer",
        "Senior Fire Risk Consultant",
    ])
    def test_facilities_risk_titles_are_blocked(self, title: str) -> None:
        m = classify_against_legacy_taxonomy(title)
        assert m.primary_taxonomy_key is None
        assert m.sub_specialism is None
