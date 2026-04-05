from vacancysoft import __version__
from vacancysoft.classifiers.taxonomy import classify_against_legacy_taxonomy
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import normalise_location
from vacancysoft.pipelines.classification import build_classification_payload
from vacancysoft.scoring.engine import compute_export_score
from vacancysoft.source_registry.seed_loader import load_seed_config


def test_version_exists() -> None:
    assert __version__ == "0.1.0"


def test_legacy_taxonomy_mapping() -> None:
    result = classify_against_legacy_taxonomy("Senior Compliance Officer")
    assert result.primary_taxonomy_key == "compliance"


def test_seed_config_loads() -> None:
    payload = load_seed_config("configs/seeds/employers.yaml")
    assert "employers" in payload


def test_classification_payload_preserves_taxonomy() -> None:
    payload = build_classification_payload("job-1", "Senior Risk Manager")
    assert payload.primary_taxonomy_key == "risk"
    assert payload.taxonomy_version == "legacy_v1"


def test_enrichment_helpers_parse_demo_values() -> None:
    assert parse_posted_date("2026-04-05") is not None
    location = normalise_location("London, UK")
    assert location["city"] == "London"
    assert location["country"] == "UK"


def test_scoring_engine_returns_weighted_value() -> None:
    score = compute_export_score(0.9, 0.8, 0.7, 0.8, 0.8, 0.9)
    assert score > 0.0
    assert score <= 1.0
