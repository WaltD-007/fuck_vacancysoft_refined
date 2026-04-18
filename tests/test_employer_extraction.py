"""Tests for employer extraction from aggregator payloads."""

from __future__ import annotations

import pytest

from vacancysoft.pipelines.enrichment_persistence import _extract_employer_from_payload


class TestExtractEmployerFromPayload:
    """_extract_employer_from_payload should find the real employer across all adapters."""

    def test_adzuna(self) -> None:
        payload = {"company": {"display_name": "Goldman Sachs"}}
        assert _extract_employer_from_payload(payload) == "Goldman Sachs"

    def test_reed(self) -> None:
        payload = {"employerName": "Barclays"}
        assert _extract_employer_from_payload(payload) == "Barclays"

    def test_google_jobs(self) -> None:
        payload = {"company_name": "JPMorgan Chase"}
        assert _extract_employer_from_payload(payload) == "JPMorgan Chase"

    def test_efinancialcareers_company_name(self) -> None:
        payload = {"companyName": "Deutsche Bank"}
        assert _extract_employer_from_payload(payload) == "Deutsche Bank"

    def test_efinancialcareers_advertiser_name(self) -> None:
        payload = {"advertiserName": "HSBC"}
        assert _extract_employer_from_payload(payload) == "HSBC"

    def test_efinancialcareers_nested_employer(self) -> None:
        payload = {"employer": {"name": "Credit Suisse"}}
        assert _extract_employer_from_payload(payload) == "Credit Suisse"

    def test_plain_company_string(self) -> None:
        payload = {"company": "Morgan Stanley"}
        assert _extract_employer_from_payload(payload) == "Morgan Stanley"

    def test_provenance_fallback(self) -> None:
        """DOM-parsed eFinancialCareers records only have company in provenance."""
        provenance = {"company": "Citi", "adapter": "efinancialcareers"}
        assert _extract_employer_from_payload(None, provenance) == "Citi"

    def test_provenance_fallback_with_empty_payload(self) -> None:
        provenance = {"company": "BlackRock"}
        assert _extract_employer_from_payload({}, provenance) == "BlackRock"

    def test_none_payload_no_provenance(self) -> None:
        assert _extract_employer_from_payload(None) is None

    def test_empty_payload(self) -> None:
        assert _extract_employer_from_payload({}) is None

    def test_whitespace_trimmed(self) -> None:
        payload = {"employerName": "  Barclays  "}
        assert _extract_employer_from_payload(payload) == "Barclays"

    def test_empty_string_ignored(self) -> None:
        payload = {"employerName": "", "companyName": "Aviva"}
        assert _extract_employer_from_payload(payload) == "Aviva"

    def test_priority_order(self) -> None:
        """Adzuna display_name should win over plain company string."""
        payload = {"company": {"display_name": "Real Employer"}, "companyName": "Wrong One"}
        assert _extract_employer_from_payload(payload) == "Real Employer"
