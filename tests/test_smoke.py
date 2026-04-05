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
from vacancysoft.adapters.workday import _job_to_record, derive_workday_candidate_endpoints


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping

# existing tests preserved in repo; added workday endpoint derivation check

def test_workday_endpoint_derivation() -> None:
    candidates = derive_workday_candidate_endpoints(
        "https://lloyds.wd3.myworkdayjobs.com/en-US/Lloyds-of-London"
    )
    assert "https://lloyds.wd3.myworkdayjobs.com/wday/cxs/lloyds/Lloyds-of-London/jobs" in candidates


def test_workday_job_mapping() -> None:
    job = {
        "title": "Senior Credit Risk Analyst",
        "locationsText": "London, UK",
        "postedOn": "2026-04-05",
        "reqId": "JR-1234",
        "externalPath": "/job/London/Senior-Credit-Risk-Analyst_JR-1234",
    }
    record = _job_to_record(job, {"job_board_url": "https://example.wd5.myworkdayjobs.com/en-US/Careers"})
    assert record.title_raw == "Senior Credit Risk Analyst"
    assert record.location_raw == "London, UK"
    assert record.posted_at_raw == "2026-04-05"
    assert record.discovered_url == "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/London/Senior-Credit-Risk-Analyst_JR-1234"
