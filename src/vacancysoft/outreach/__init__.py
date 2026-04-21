"""Outreach email — Microsoft Graph integration.

Module surface (see docs/outreach_email.md for the full design):

- ``is_dry_run()``               — master kill-switch check
- ``SecretClient``               — fetches client secret from Key Vault or env
- ``GraphClient``                — async Graph API wrapper (send_mail, list_replies)
- ``GraphError``                 — canonical error type all callers catch

Everything routes through ``is_dry_run()`` — when true (the default) the
Graph client returns canned responses and never touches the network, so
the stack is safe to run in any environment without Entra credentials.
"""

from __future__ import annotations

from vacancysoft.outreach.dry_run import is_dry_run
from vacancysoft.outreach.graph_client import GraphClient, GraphError
from vacancysoft.outreach.secret_client import SecretClient

__all__ = [
    "is_dry_run",
    "GraphClient",
    "GraphError",
    "SecretClient",
]
