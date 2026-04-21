"""Tests for the outreach SecretClient.

Precedence:
  1. Dry-run → DRY_RUN_SECRET placeholder (no I/O)
  2. KEY_VAULT_URI set → Key Vault path
  3. GRAPH_CLIENT_SECRET set → env path
  4. Nothing → SecretClientError

The Key Vault path is tested via a fake ``azure.keyvault.secrets`` module
injected into ``sys.modules`` so we don't need the real Azure SDK to
exercise the control-flow branches.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from vacancysoft.outreach.secret_client import (
    DRY_RUN_PLACEHOLDER,
    SecretClient,
    SecretClientError,
)


class _FakeKvSecret:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeKvClient:
    """Stand-in for ``azure.keyvault.secrets.SecretClient``. Tracks the
    last secret_name it was asked for so tests can assert against it."""

    last_requested_name: str | None = None
    next_value: str = "live-secret-value"
    raise_on_get: Exception | None = None

    def __init__(self, *, vault_url: str, credential: Any) -> None:
        self.vault_url = vault_url
        self.credential = credential

    def get_secret(self, name: str) -> _FakeKvSecret:
        _FakeKvClient.last_requested_name = name
        if _FakeKvClient.raise_on_get is not None:
            raise _FakeKvClient.raise_on_get
        return _FakeKvSecret(_FakeKvClient.next_value)


class _FakeDefaultAzureCredential:
    def __init__(self) -> None:
        pass


@pytest.fixture
def fake_azure_sdks(monkeypatch: pytest.MonkeyPatch):
    """Inject fake azure-identity + azure-keyvault-secrets modules so the
    Key Vault code path can be exercised without the real SDK."""
    # Reset class-level tracking
    _FakeKvClient.last_requested_name = None
    _FakeKvClient.next_value = "live-secret-value"
    _FakeKvClient.raise_on_get = None

    identity_mod = types.ModuleType("azure.identity")
    identity_mod.DefaultAzureCredential = _FakeDefaultAzureCredential  # type: ignore[attr-defined]

    secrets_mod = types.ModuleType("azure.keyvault.secrets")
    secrets_mod.SecretClient = _FakeKvClient  # type: ignore[attr-defined]

    # Parent "azure" and "azure.keyvault" must exist for the `from` imports
    # to resolve through the normal import machinery.
    azure_mod = sys.modules.get("azure") or types.ModuleType("azure")
    kv_mod = sys.modules.get("azure.keyvault") or types.ModuleType("azure.keyvault")

    monkeypatch.setitem(sys.modules, "azure", azure_mod)
    monkeypatch.setitem(sys.modules, "azure.keyvault", kv_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", secrets_mod)
    yield _FakeKvClient


class TestDryRunPath:
    """Default (no env vars) + explicit dry-run both return the placeholder."""

    def test_default_returns_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)
        monkeypatch.delenv("KEY_VAULT_URI", raising=False)
        monkeypatch.delenv("GRAPH_CLIENT_SECRET", raising=False)

        client = SecretClient()
        assert client.get_client_secret() == DRY_RUN_PLACEHOLDER

    def test_explicit_dry_run_true_returns_placeholder_even_with_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If dry-run is explicitly on, we must NOT touch Key Vault or env."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "true")
        monkeypatch.setenv("GRAPH_CLIENT_SECRET", "real-secret-would-leak")
        monkeypatch.setenv("KEY_VAULT_URI", "https://should-not-be-called")

        assert SecretClient().get_client_secret() == DRY_RUN_PLACEHOLDER


class TestKeyVaultPath:
    """When OUTREACH_DRY_RUN=false + KEY_VAULT_URI is set."""

    def test_fetches_from_key_vault(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_azure_sdks: type[_FakeKvClient],
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example.net")
        monkeypatch.delenv("GRAPH_CLIENT_SECRET", raising=False)

        fake_azure_sdks.next_value = "prod-secret-from-kv"

        out = SecretClient().get_client_secret()
        assert out == "prod-secret-from-kv"
        assert fake_azure_sdks.last_requested_name == "prospero-graph-client-secret"

    def test_honours_custom_secret_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_azure_sdks: type[_FakeKvClient],
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example.net")

        SecretClient(secret_name="custom-name").get_client_secret()
        assert fake_azure_sdks.last_requested_name == "custom-name"

    def test_honours_env_override_of_secret_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_azure_sdks: type[_FakeKvClient],
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example.net")
        monkeypatch.setenv("GRAPH_CLIENT_SECRET_NAME", "alt-secret")

        SecretClient().get_client_secret()
        assert fake_azure_sdks.last_requested_name == "alt-secret"

    def test_key_vault_failure_raises_secret_client_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_azure_sdks: type[_FakeKvClient],
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example.net")
        fake_azure_sdks.raise_on_get = RuntimeError("Forbidden")

        with pytest.raises(SecretClientError) as exc:
            SecretClient().get_client_secret()
        assert "Forbidden" in str(exc.value)

    def test_empty_secret_from_kv_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_azure_sdks: type[_FakeKvClient],
    ) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.setenv("KEY_VAULT_URI", "https://kv.example.net")
        fake_azure_sdks.next_value = ""

        with pytest.raises(SecretClientError):
            SecretClient().get_client_secret()


class TestEnvVarPath:
    """When OUTREACH_DRY_RUN=false + no KEY_VAULT_URI + GRAPH_CLIENT_SECRET is set.
    This is the local-dev path."""

    def test_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.delenv("KEY_VAULT_URI", raising=False)
        monkeypatch.setenv("GRAPH_CLIENT_SECRET", "dev-only-secret")

        assert SecretClient().get_client_secret() == "dev-only-secret"

    def test_empty_env_var_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.delenv("KEY_VAULT_URI", raising=False)
        monkeypatch.setenv("GRAPH_CLIENT_SECRET", "   ")

        with pytest.raises(SecretClientError):
            SecretClient().get_client_secret()

    def test_both_unset_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "false")
        monkeypatch.delenv("KEY_VAULT_URI", raising=False)
        monkeypatch.delenv("GRAPH_CLIENT_SECRET", raising=False)

        with pytest.raises(SecretClientError) as exc:
            SecretClient().get_client_secret()
        # Message should guide the dev to the fix
        assert "KEY_VAULT_URI" in str(exc.value) or "OUTREACH_DRY_RUN" in str(exc.value)
