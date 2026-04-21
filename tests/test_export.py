"""Tests for export views and serialisers."""

from __future__ import annotations

from datetime import datetime

import pytest

from vacancysoft.exporters.views import _base_export_query, load_exporter_config
from vacancysoft.exporters.serialisers import (
    LEGACY_EXPORT_COLUMNS,
    _build_job_ref,
    _safe_str,
    row_to_legacy_lead,
)


class TestExporterConfig:
    """Exporter config should load profiles and client segments."""

    def test_config_loads(self) -> None:
        config = load_exporter_config()
        assert isinstance(config, dict)

    def test_profiles_exist(self) -> None:
        config = load_exporter_config()
        profiles = config.get("profiles", {})
        assert "accepted_only" in profiles
        assert "accepted_plus_review" in profiles

    def test_client_segments_exist(self) -> None:
        config = load_exporter_config()
        segments = config.get("client_segments", {})
        assert "risk_only" in segments
        assert "control_functions" in segments
        assert "front_office" in segments


class TestSerialisers:
    """Legacy serialiser helpers should work correctly."""

    def test_export_columns_defined(self) -> None:
        assert len(LEGACY_EXPORT_COLUMNS) >= 10
        assert "Job URL" in LEGACY_EXPORT_COLUMNS
        assert "Job Title" in LEGACY_EXPORT_COLUMNS
        assert "Company" in LEGACY_EXPORT_COLUMNS
        assert "Country" in LEGACY_EXPORT_COLUMNS

    def test_safe_str_none(self) -> None:
        assert _safe_str(None) == ""

    def test_safe_str_list(self) -> None:
        assert _safe_str(["London", "UK"]) == "London, UK"

    def test_safe_str_string(self) -> None:
        assert _safe_str("hello") == "hello"

    def test_job_ref_deterministic(self) -> None:
        ref1 = _build_job_ref("Barclays", "Risk Analyst", "London", "UK", "https://x.com/1", "2026-01-01", "workday")
        ref2 = _build_job_ref("Barclays", "Risk Analyst", "London", "UK", "https://x.com/1", "2026-01-01", "workday")
        assert ref1 == ref2

    def test_job_ref_unique(self) -> None:
        ref1 = _build_job_ref("Barclays", "Risk Analyst", "London", "UK", "https://x.com/1", "2026-01-01", "workday")
        ref2 = _build_job_ref("HSBC", "Risk Analyst", "London", "UK", "https://x.com/2", "2026-01-01", "workday")
        assert ref1 != ref2

    def test_job_ref_format(self) -> None:
        ref = _build_job_ref("Barclays", "Risk Analyst", "London", "UK", "https://x.com/1", "2026-01-01", "workday")
        assert ref.startswith("lead-")


class TestDateScrapedColumn:
    """'Date Scraped' must reflect RawJob.first_seen_at (the scraper's
    first-discovery timestamp) — not the export run time.

    Regression guard against the previous behaviour where the column
    was `datetime.now().date().isoformat()`, which made every row show
    the day the report was run rather than the day the lead was found.
    """

    def test_base_export_query_selects_first_seen_at(self) -> None:
        """The SELECT list must carry first_seen_at so the serialiser
        has something to read from. If this assertion fires, the
        serialiser falls back to empty string (silent data loss)."""
        stmt = _base_export_query()
        column_names = {col.name for col in stmt.selected_columns}
        assert "first_seen_at" in column_names

    def test_row_to_legacy_lead_uses_first_seen_at(self) -> None:
        """The 'Date Scraped' column pulls from the mapping, not
        `datetime.now()`. Using a hand-rolled mapping (not an actual
        ORM row) keeps the test DB-free."""
        scrape_date = datetime(2025, 11, 14)
        mapping = {
            "title": "Head of Credit Risk",
            "employer_name": "Barclays",
            "discovered_url": "https://example.com/job/1",
            "apply_url": "https://example.com/job/1",
            "source_key": "barclays-greenhouse",
            "location_text": "London",
            "location_country": "United Kingdom",
            "primary_taxonomy_key": "risk",
            "posted_at": "2025-11-10",
            "first_seen_at": scrape_date,
        }
        row = row_to_legacy_lead(mapping)
        assert row["Date Scraped"] == "2025-11-14"
        # Sanity: not today's date (the old buggy behaviour).
        assert row["Date Scraped"] != datetime.now().date().isoformat() or scrape_date.date() == datetime.now().date()

    def test_row_to_legacy_lead_missing_first_seen_at(self) -> None:
        """Defensive — if the query somehow omits the column (e.g. a
        future refactor drops it) the serialiser falls back to empty
        string rather than crashing or reverting to export-run time."""
        mapping = {
            "title": "Risk Analyst",
            "employer_name": "HSBC",
            "discovered_url": "https://example.com/job/2",
            "apply_url": "https://example.com/job/2",
            "source_key": "hsbc-workday",
            "location_text": "London",
            "location_country": "United Kingdom",
            "primary_taxonomy_key": "risk",
            "posted_at": "2025-11-12",
            # first_seen_at deliberately missing
        }
        row = row_to_legacy_lead(mapping)
        assert row["Date Scraped"] == ""
