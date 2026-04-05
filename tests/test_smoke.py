from vacancysoft import __version__
from vacancysoft.classifiers.taxonomy import classify_against_legacy_taxonomy
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import normalise_location
from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category, map_sub_specialism, normalise_country
from vacancysoft.exporters.serialisers import build_legacy_webhook_payload, row_to_legacy_lead
from vacancysoft.exporters.views import load_exporter_config
from vacancysoft.pipelines.classification import build_classification_payload
from vacancysoft.scoring.engine import compute_export_score
from vacancysoft.source_registry.seed_loader import load_seed_config
from vacancysoft.adapters.workday import _job_to_record


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


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


def test_exporter_config_loads() -> None:
    config = load_exporter_config()
    assert "client_segments" in config
    assert "risk_only" in config["client_segments"]
    assert "profiles" in config
    assert "accepted_only" in config["profiles"]
    assert config["webhook"]["production_url"] == "https://antonyberou.app.n8n.cloud/webhook/fuck_vacancysoft"


def test_legacy_webhook_payload_shape() -> None:
    row = _FakeRow(
        {
            "title": "Senior Risk Manager at Example Capital",
            "location_text": "London, UK",
            "location_country": "UK",
            "primary_taxonomy_key": "risk",
            "secondary_taxonomy_keys": [],
            "employer_name": "Example Capital",
            "discovered_url": "https://example.com/job",
            "apply_url": "https://example.com/apply",
            "source_key": "greenhouse",
            "posted_at": "2026-04-05",
        }
    )
    legacy = row_to_legacy_lead(row)
    assert legacy["Company"] == "Example Capital"
    assert legacy["Job Title"] == "Senior Risk Manager at Example Capital"
    assert legacy["Category"] == "Risk"
    assert legacy["Sub Specialism"] == "Risk Management"
    assert legacy["Country"] in {"N/A", "UK", "United Kingdom"}
    payload = build_legacy_webhook_payload([row])
    assert "body" in payload
    assert isinstance(payload["body"], list)
    assert payload["body"][0]["Job URL"] == "https://example.com/job"


def test_legacy_routing_loads_and_maps() -> None:
    routing = load_legacy_routing()
    assert map_category("compliance", routing) == "Compliance"
    assert map_sub_specialism("Senior Quantitative Developer", "Quant", routing) == "Quantitative Development"
    assert normalise_country("usa", routing) == "USA"


def test_workday_job_mapping() -> None:
    job = {
        "title": "Senior Credit Risk Analyst",
        "locationsText": "London, UK",
        "postedOn": "2026-04-05",
        "bulletFields": ["JR-1234"],
        "externalPath": "/job/London/Senior-Credit-Risk-Analyst_JR-1234",
    }
    record = _job_to_record(job, {"job_board_url": "https://example.wd5.myworkdayjobs.com/en-US/Careers"})
    assert record.title_raw == "Senior Credit Risk Analyst"
    assert record.location_raw == "London, UK"
    assert record.posted_at_raw == "2026-04-05"
    assert record.discovered_url == "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/London/Senior-Credit-Risk-Analyst_JR-1234"
