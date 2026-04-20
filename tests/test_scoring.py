"""Tests for the scoring engine: weights, thresholds, and decisions."""

from __future__ import annotations

import pytest

from vacancysoft.scoring.engine import (
    compute_export_score,
    decision_from_score,
    get_thresholds,
    get_weights,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestScoringConfig:
    """Weights and thresholds should load from scoring.toml."""

    def test_weights_load(self) -> None:
        w = get_weights()
        assert isinstance(w, dict)
        expected_keys = {
            "title_relevance", "location_confidence", "freshness_confidence",
            "source_reliability", "completeness", "classification_confidence",
        }
        assert expected_keys.issubset(set(w.keys()))

    def test_weights_sum_to_one(self) -> None:
        w = get_weights()
        total = sum(w.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected ~1.0"

    def test_thresholds_load(self) -> None:
        t = get_thresholds()
        assert "accepted" in t
        assert "review" in t
        assert t["accepted"] > t["review"]


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

class TestComputeExportScore:
    """compute_export_score should produce a weighted sum in [0, 1]."""

    def test_perfect_score(self) -> None:
        score = compute_export_score(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        assert score == 1.0

    def test_zero_score(self) -> None:
        score = compute_export_score(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert score == 0.0

    def test_typical_good_lead(self) -> None:
        score = compute_export_score(
            title_relevance=0.95,
            location_confidence=0.85,
            freshness_confidence=0.85,
            source_reliability=0.80,
            completeness=0.75,
            classification_confidence=0.90,
        )
        assert score > 0.75, f"Good lead should score > 0.75, got {score}"

    def test_weak_lead(self) -> None:
        score = compute_export_score(
            title_relevance=0.15,
            location_confidence=0.30,
            freshness_confidence=0.30,
            source_reliability=0.50,
            completeness=0.25,
            classification_confidence=0.30,
        )
        assert score < 0.45, f"Weak lead should score < 0.45, got {score}"

    def test_score_is_rounded(self) -> None:
        score = compute_export_score(0.333, 0.333, 0.333, 0.333, 0.333, 0.333)
        assert score == round(score, 4)


# ---------------------------------------------------------------------------
# Decision from score
# ---------------------------------------------------------------------------

class TestDecisionFromScore:
    """decision_from_score should use thresholds from scoring.toml."""

    def test_accepted(self) -> None:
        t = get_thresholds()
        assert decision_from_score(t["accepted"]) == "accepted"
        assert decision_from_score(0.99) == "accepted"

    def test_review(self) -> None:
        t = get_thresholds()
        assert decision_from_score(t["review"]) == "review"
        # Just below accepted
        assert decision_from_score(t["accepted"] - 0.01) == "review"

    def test_rejected(self) -> None:
        t = get_thresholds()
        assert decision_from_score(t["review"] - 0.01) == "rejected"
        assert decision_from_score(0.0) == "rejected"

    def test_boundary_accepted(self) -> None:
        """Score exactly at accepted threshold should be accepted."""
        t = get_thresholds()
        assert decision_from_score(t["accepted"]) == "accepted"

    def test_boundary_review(self) -> None:
        """Score exactly at review threshold should be review."""
        t = get_thresholds()
        assert decision_from_score(t["review"]) == "review"
