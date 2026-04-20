"""Tests for adapter registry and new adapters."""

from __future__ import annotations

import pytest

from vacancysoft.adapters import ADAPTER_REGISTRY
from vacancysoft.adapters.base import SourceAdapter


class TestAdapterRegistry:
    """All adapters should auto-discover and be properly registered."""

    EXPECTED_ADAPTERS = [
        "workday", "greenhouse", "workable", "ashby", "smartrecruiters",
        "lever", "icims", "oracle", "successfactors", "eightfold",
        "generic_site", "adzuna", "reed", "efinancialcareers", "google_jobs",
        "hibob", "selectminds", "silkroad", "taleo",
    ]

    def test_registry_is_populated(self) -> None:
        assert len(ADAPTER_REGISTRY) >= 19

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_adapter_registered(self, name: str) -> None:
        assert name in ADAPTER_REGISTRY, f"Adapter '{name}' not found in registry"

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_adapter_is_source_adapter(self, name: str) -> None:
        cls = ADAPTER_REGISTRY[name]
        assert issubclass(cls, SourceAdapter)

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_adapter_has_capabilities(self, name: str) -> None:
        cls = ADAPTER_REGISTRY[name]
        assert hasattr(cls, "capabilities")
        caps = cls.capabilities
        assert caps.supports_discovery is True

    @pytest.mark.parametrize("name", EXPECTED_ADAPTERS)
    def test_adapter_name_matches_key(self, name: str) -> None:
        cls = ADAPTER_REGISTRY[name]
        assert cls.adapter_name == name


class TestNewAdapters:
    """The 4 new adapters should have correct capability flags."""

    def test_hibob_is_browser(self) -> None:
        cls = ADAPTER_REGISTRY["hibob"]
        assert cls.capabilities.supports_browser is True
        assert cls.capabilities.supports_api is False

    def test_selectminds_is_browser(self) -> None:
        cls = ADAPTER_REGISTRY["selectminds"]
        assert cls.capabilities.supports_browser is True
        assert cls.capabilities.supports_api is False

    def test_silkroad_is_api(self) -> None:
        cls = ADAPTER_REGISTRY["silkroad"]
        assert cls.capabilities.supports_api is True
        assert cls.capabilities.supports_browser is False

    def test_taleo_is_api(self) -> None:
        cls = ADAPTER_REGISTRY["taleo"]
        assert cls.capabilities.supports_api is True
        assert cls.capabilities.supports_browser is False
        assert cls.capabilities.supports_pagination is True


class TestLeverSlugFallback:
    """Lever adapter should derive slug from a jobs.lever.co URL when the
    `slug` key is absent from config, and raise a clear error only when
    neither slug nor a matching URL is present.
    """

    def test_derives_slug_from_plain_url(self) -> None:
        from vacancysoft.adapters.lever import _derive_slug_from_url

        assert _derive_slug_from_url("https://jobs.lever.co/octoenergy") == "octoenergy"

    def test_derives_slug_from_url_with_trailing_slash(self) -> None:
        from vacancysoft.adapters.lever import _derive_slug_from_url

        assert _derive_slug_from_url("https://jobs.lever.co/contentsquare/") == "contentsquare"

    def test_derives_slug_from_url_with_path(self) -> None:
        from vacancysoft.adapters.lever import _derive_slug_from_url

        # Even if someone pastes a full posting URL, the slug extractor
        # returns the board slug (the first path segment).
        assert _derive_slug_from_url("https://jobs.lever.co/titanwh/abc123") == "titanwh"

    def test_rejects_non_lever_url(self) -> None:
        from vacancysoft.adapters.lever import _derive_slug_from_url

        assert _derive_slug_from_url("https://www.vaneck.com/us/en/careers/") is None

    def test_rejects_empty_or_none(self) -> None:
        from vacancysoft.adapters.lever import _derive_slug_from_url

        assert _derive_slug_from_url(None) is None
        assert _derive_slug_from_url("") is None
        assert _derive_slug_from_url("   ") is None

    @pytest.mark.asyncio
    async def test_discover_raises_clear_error_when_slug_underivable(self) -> None:
        """VanEck case: no slug, URL isn't a Lever one. Should raise ValueError
        that names the offending URL so operators can spot the mis-classification."""
        from vacancysoft.adapters.lever import LeverAdapter

        adapter = LeverAdapter()
        with pytest.raises(ValueError, match=r"jobs\.lever\.co"):
            await adapter.discover(source_config={
                "job_board_url": "https://www.vaneck.com/us/en/careers/",
            })

    @pytest.mark.asyncio
    async def test_discover_raises_clear_error_when_nothing_provided(self) -> None:
        """No slug and no URL. Should still raise ValueError."""
        from vacancysoft.adapters.lever import LeverAdapter

        adapter = LeverAdapter()
        with pytest.raises(ValueError, match="slug"):
            await adapter.discover(source_config={})
