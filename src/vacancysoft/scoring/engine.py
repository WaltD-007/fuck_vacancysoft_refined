from __future__ import annotations

from pathlib import Path

import tomllib

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "scoring.toml"

_config_cache: dict | None = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, "rb") as f:
            _config_cache = tomllib.load(f)
    return _config_cache


def get_weights() -> dict[str, float]:
    return dict(_load_config().get("weights", {}))


def get_thresholds() -> dict[str, float]:
    return dict(_load_config().get("thresholds", {}))


def compute_export_score(
    title_relevance: float,
    location_confidence: float,
    freshness_confidence: float,
    source_reliability: float,
    completeness: float,
    classification_confidence: float,
) -> float:
    w = get_weights()
    return round(
        w.get("title_relevance", 0.30) * title_relevance
        + w.get("location_confidence", 0.15) * location_confidence
        + w.get("freshness_confidence", 0.15) * freshness_confidence
        + w.get("source_reliability", 0.10) * source_reliability
        + w.get("completeness", 0.10) * completeness
        + w.get("classification_confidence", 0.20) * classification_confidence,
        4,
    )


def decision_from_score(score: float) -> str:
    t = get_thresholds()
    if score >= t.get("accepted", 0.75):
        return "accepted"
    if score >= t.get("review", 0.45):
        return "review"
    return "rejected"
