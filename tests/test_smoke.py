from vacancysoft import __version__
from vacancysoft.adapters.adzuna import _format_salary, _parse_job
from vacancysoft.adapters.workday import _job_to_record, derive_workday_candidate_endpoints


def test_version_exists() -> None:
    assert __version__ == "0.1.0"


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


def test_adzuna_salary_formatting() -> None:
    assert _format_salary({"salary_min": 90000, "salary_max": 120000, "salary_currency": "GBP"}) == "£90,000 - £120,000"


def test_adzuna_job_mapping() -> None:
    job = {
        "id": "123",
        "title": "Senior Compliance Officer",
        "company": {"display_name": "Example Bank"},
        "location": {"display_name": "London", "area": ["UK", "England", "London"]},
        "redirect_url": "https://www.adzuna.co.uk/jobs/details/123",
        "created": "2026-04-05T12:00:00Z",
        "description": "Great role",
        "salary_min": 80000,
        "salary_max": 95000,
        "salary_currency": "GBP",
        "contract_time": "full_time",
    }
    record = _parse_job(job, "https://www.adzuna.com/gb/search")
    assert record.title_raw == "Senior Compliance Officer"
    assert record.location_raw == "London"
    assert record.discovered_url == "https://www.adzuna.co.uk/jobs/details/123"
    assert record.provenance["company"] == "Example Bank"
    assert record.provenance["salary"] == "£80,000 - £95,000"
