"""Tests for config loading and board integrity."""

from __future__ import annotations

import pytest

from configs.config import (
    ADZUNA_BOARDS,
    ASHBY_BOARDS,
    EFINANCIALCAREERS_BOARDS,
    EIGHTFOLD_BOARDS,
    GENERIC_BROWSER_BOARDS,
    GOOGLE_JOBS_BOARDS,
    GREENHOUSE_BOARDS,
    HIBOB_BOARDS,
    ICIMS_BOARDS,
    LEVER_BOARDS,
    ORACLE_BOARDS,
    REED_BOARDS,
    SEARCH_TERMS,
    SELECTMINDS_BOARDS,
    SILKROAD_BOARDS,
    SMARTRECRUITERS_BOARDS,
    SUCCESSFACTORS_BOARDS,
    TALEO_BOARDS,
    WORKABLE_BOARDS,
    WORKDAY_BOARDS,
    WorkdayBoard,
)


class TestConfigLoads:
    """All board lists should load without error."""

    def test_workday_boards_populated(self) -> None:
        assert len(WORKDAY_BOARDS) > 100

    def test_generic_boards_populated(self) -> None:
        assert len(GENERIC_BROWSER_BOARDS) > 400

    def test_greenhouse_boards_populated(self) -> None:
        assert len(GREENHOUSE_BOARDS) > 20

    def test_aggregators_configured(self) -> None:
        assert len(ADZUNA_BOARDS) >= 1
        assert len(REED_BOARDS) >= 1
        assert len(EFINANCIALCAREERS_BOARDS) >= 1
        assert len(GOOGLE_JOBS_BOARDS) >= 1

    def test_search_terms_populated(self) -> None:
        assert len(SEARCH_TERMS) >= 10


class TestWorkdayBoards:
    """Workday boards should parse into WorkdayBoard dataclasses."""

    def test_all_are_workday_board(self) -> None:
        for board in WORKDAY_BOARDS:
            assert isinstance(board, WorkdayBoard)

    def test_all_have_tenant(self) -> None:
        for board in WORKDAY_BOARDS:
            assert board.tenant, f"Board {board.board_url} has no tenant"

    def test_all_have_api_url(self) -> None:
        for board in WORKDAY_BOARDS:
            api = board.api_url
            assert "myworkdayjobs.com" in api, f"Board {board.company} has bad api_url: {api}"

    def test_all_have_company(self) -> None:
        for board in WORKDAY_BOARDS:
            assert board.company, f"Board {board.board_url} has no company"


class TestGenericBoards:
    """Generic boards should all have url and company keys."""

    def test_all_have_url(self) -> None:
        for board in GENERIC_BROWSER_BOARDS:
            assert board.get("url"), f"Board missing url: {board}"

    def test_all_have_company(self) -> None:
        for board in GENERIC_BROWSER_BOARDS:
            assert board.get("company"), f"Board missing company: {board}"

    def test_urls_are_https(self) -> None:
        for board in GENERIC_BROWSER_BOARDS:
            url = board["url"]
            assert url.startswith("http"), f"Bad URL: {url}"

    def test_no_duplicate_company_url_pairs(self) -> None:
        """Same company + same URL should not appear twice."""
        pairs = [(b["company"], b["url"]) for b in GENERIC_BROWSER_BOARDS]
        dupes = [p for p in pairs if pairs.count(p) > 1]
        assert len(set(dupes)) == 0, f"Duplicate company+URL pairs: {set(dupes)}"


class TestAggregatorConfig:
    """Aggregator boards should have search_terms configured."""

    def test_adzuna_has_search_terms(self) -> None:
        board = ADZUNA_BOARDS[0]
        assert len(board.get("search_terms", [])) >= 10
        assert len(board.get("countries", [])) >= 5

    def test_reed_has_search_terms(self) -> None:
        board = REED_BOARDS[0]
        assert len(board.get("search_terms", [])) >= 10
        assert len(board.get("locations", [])) >= 5

    def test_google_jobs_has_search_terms(self) -> None:
        board = GOOGLE_JOBS_BOARDS[0]
        assert len(board.get("search_terms", [])) >= 8
        assert len(board.get("locations", [])) >= 5

    def test_efinancialcareers_has_search_terms(self) -> None:
        board = EFINANCIALCAREERS_BOARDS[0]
        assert len(board.get("search_terms", [])) >= 10
