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
