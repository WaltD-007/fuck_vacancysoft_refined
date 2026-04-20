"""Tests for export views and serialisers."""

from __future__ import annotations

import pytest

from vacancysoft.exporters.views import load_exporter_config
from vacancysoft.exporters.serialisers import (
    LEGACY_EXPORT_COLUMNS,
    _build_job_ref,
    _safe_str,
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
