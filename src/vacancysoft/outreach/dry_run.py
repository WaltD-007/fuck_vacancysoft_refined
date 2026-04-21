"""Dry-run kill switch for outreach email.

One function: :func:`is_dry_run`. Every Graph-touching code path calls it
at the top. When it returns True (the default) those paths take a canned
synthetic branch instead of hitting the network.

Decision rule:

  OUTREACH_DRY_RUN unset                → True   (default — safe)
  OUTREACH_DRY_RUN='true'               → True
  OUTREACH_DRY_RUN='1'                  → True
  OUTREACH_DRY_RUN='yes'                → True
  OUTREACH_DRY_RUN='false'              → False
  OUTREACH_DRY_RUN='0'                  → False
  OUTREACH_DRY_RUN='no'                 → False
  OUTREACH_DRY_RUN=<anything else>      → True   (fail-safe on typos)

This deliberately does NOT cache the result. Reading env vars per-call
costs nothing and means operators can flip the switch at runtime by
updating the Container App env var without a full restart — useful for
an emergency freeze.

For synthesising canned responses in dry-run, see :func:`canned_send_mail`
and :func:`canned_list_replies` below. Those are called by ``GraphClient``
when the kill-switch is on.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any


_TRUTHY = {"true", "1", "yes", "y", "on"}
_FALSY = {"false", "0", "no", "n", "off"}


def is_dry_run() -> bool:
    """Return True iff the master kill-switch says "do not hit Graph".

    Default: True. Only flipping ``OUTREACH_DRY_RUN`` to an explicit
    falsy value returns False. Any other value (typos, whitespace, empty
    string from an accidentally-unset secret) returns True — the safe
    side of the read.
    """
    raw = os.environ.get("OUTREACH_DRY_RUN")
    if raw is None:
        return True
    normalised = raw.strip().lower()
    if normalised in _FALSY:
        return False
    # Truthy, empty, or anything unrecognised → safe side.
    return True


def canned_send_mail(
    *,
    user_id: str,
    to_address: str,
    subject: str,
) -> dict[str, str]:
    """Return the dict shape :meth:`GraphClient.send_mail` would return on
    a successful real call — with synthetic ids.

    The ids are deterministic-per-call (uuid4) and clearly-fake (prefixed
    ``dryrun-``) so any downstream logging / DB write can distinguish
    real vs canned output at a glance.
    """
    msg_id = f"dryrun-msg-{uuid.uuid4().hex[:16]}"
    conv_id = f"dryrun-conv-{uuid.uuid4().hex[:22]}"
    return {
        "graph_message_id": msg_id,
        "conversation_id": conv_id,
        "user_id": user_id,
        "to_address": to_address,
        "subject": subject,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
    }


def canned_list_replies(
    *,
    user_id: str,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Return the list shape :meth:`GraphClient.list_replies` would return.

    Dry-run always returns an empty list. This means the reply-polling
    task never fires the "sequence cancelled due to reply" path in
    dry-run mode, which is realistic — we don't want to pretend replies
    arrived when no mail was actually sent.

    If a test needs the cancel-on-reply path exercised, it should patch
    this function directly rather than relying on the env var.
    """
    # Args accepted for signature-symmetry with the real client — ignored here.
    del user_id, conversation_id
    return []
