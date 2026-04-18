"""Tests for the classification pipeline: title rules and taxonomy."""

from __future__ import annotations

import pytest

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
        "Pricing Actuary",
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
