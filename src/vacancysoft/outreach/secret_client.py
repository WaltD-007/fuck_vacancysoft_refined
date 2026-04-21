"""Client-secret fetcher for the Microsoft Graph integration.

Two paths:

1. **Key Vault** (production). When ``KEY_VAULT_URI`` is set, we use
   ``azure-identity.DefaultAzureCredential`` + ``azure-keyvault-secrets``
   to pull the secret by name. The Container App's system-assigned
   managed identity must have ``get`` permission on the vault.

2. **Env var** (local dev only). When ``KEY_VAULT_URI`` is unset, we
   read ``GRAPH_CLIENT_SECRET`` from the environment. This path is NOT
   used in production — the absence of ``KEY_VAULT_URI`` on a prod
   Container App is a misconfiguration and the app should alarm.

3. **Dry-run** (default, any env). When :func:`~vacancysoft.outreach.dry_run.is_dry_run`
   returns True, we return the literal string ``DRY_RUN_SECRET`` without
   touching Key Vault, env, or anything else. That value is meaningless
   to Graph — the ``GraphClient`` short-circuits well before it would
   be used — so it's fine that it's obviously fake.

Azure SDKs are imported lazily inside :meth:`get_client_secret` so that
(a) tests don't need them installed, and (b) the dry-run path on a
local dev machine without the Azure libs installed still works.
"""

from __future__ import annotations

import logging
import os

from vacancysoft.outreach.dry_run import is_dry_run

logger = logging.getLogger(__name__)


DEFAULT_SECRET_NAME = "prospero-graph-client-secret"
DRY_RUN_PLACEHOLDER = "DRY_RUN_SECRET"  # Must not be a real secret shape. Never sent to Graph.


class SecretClientError(RuntimeError):
    """Raised when the secret cannot be retrieved (not in dry-run)."""


class SecretClient:
    """Fetches the Graph client secret from Key Vault or env var.

    Usage::

        client = SecretClient()
        secret = client.get_client_secret()

    The class is a thin wrapper so tests can substitute a stub. The real
    state (Key Vault URI, env var values) all comes from environment
    variables read at call time, not at __init__ — flipping Key Vault
    on or off doesn't require re-instantiating the client.
    """

    def __init__(self, *, secret_name: str | None = None) -> None:
        self.secret_name = secret_name or os.environ.get(
            "GRAPH_CLIENT_SECRET_NAME", DEFAULT_SECRET_NAME
        )

    def get_client_secret(self) -> str:
        """Return the Graph app-registration client secret.

        Precedence:
          1. Dry-run (env default) → ``DRY_RUN_SECRET``
          2. ``KEY_VAULT_URI`` set → Key Vault path
          3. Otherwise → ``GRAPH_CLIENT_SECRET`` env var
          4. Nothing found → :class:`SecretClientError`
        """
        if is_dry_run():
            logger.debug(
                "outreach.secret_client: dry-run — returning placeholder secret"
            )
            return DRY_RUN_PLACEHOLDER

        key_vault_uri = os.environ.get("KEY_VAULT_URI", "").strip()
        if key_vault_uri:
            return self._fetch_from_key_vault(key_vault_uri)

        env_secret = os.environ.get("GRAPH_CLIENT_SECRET", "").strip()
        if env_secret:
            logger.warning(
                "outreach.secret_client: reading GRAPH_CLIENT_SECRET from env. "
                "This path is for local dev only — production should route via "
                "KEY_VAULT_URI."
            )
            return env_secret

        raise SecretClientError(
            "No client secret available. Either set OUTREACH_DRY_RUN=true (the "
            "default), or set KEY_VAULT_URI (production) or GRAPH_CLIENT_SECRET "
            "(dev)."
        )

    def _fetch_from_key_vault(self, key_vault_uri: str) -> str:
        """Fetch the secret from Azure Key Vault using managed identity."""
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-untyped]
            from azure.keyvault.secrets import SecretClient as KvClient  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover — covered by a specific test
            raise SecretClientError(
                "KEY_VAULT_URI is set but the Azure SDKs are not installed. "
                "Install `azure-identity` and `azure-keyvault-secrets`, or flip "
                "OUTREACH_DRY_RUN=true for a dev environment."
            ) from exc

        try:
            credential = DefaultAzureCredential()
            kv = KvClient(vault_url=key_vault_uri, credential=credential)
            secret = kv.get_secret(self.secret_name)
        except Exception as exc:
            raise SecretClientError(
                f"Failed to fetch secret {self.secret_name!r} from "
                f"{key_vault_uri}: {exc}"
            ) from exc

        value = getattr(secret, "value", None)
        if not value:
            raise SecretClientError(
                f"Key Vault returned an empty secret for {self.secret_name!r}"
            )
        logger.debug(
            "outreach.secret_client: fetched secret %r from Key Vault",
            self.secret_name,
        )
        return value
