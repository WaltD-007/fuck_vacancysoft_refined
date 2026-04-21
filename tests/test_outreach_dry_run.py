"""Tests for the outreach dry-run kill-switch.

Default policy: unset → True. Explicit falsy → False. Anything else → True
(fail-safe on typos). Canned responses are deterministic-shape, synthetic-id.
"""

from __future__ import annotations

import os

import pytest

from vacancysoft.outreach.dry_run import (
    canned_list_replies,
    canned_send_mail,
    is_dry_run,
)


class TestIsDryRun:
    """Kill-switch env-var parsing."""

    def _clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_DRY_RUN", raising=False)

    def test_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._clear(monkeypatch)
        assert is_dry_run() is True

    @pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", "Y", "on", "ON"])
    def test_truthy_values_are_true(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", val)
        assert is_dry_run() is True

    @pytest.mark.parametrize("val", ["false", "FALSE", "False", "0", "no", "n", "off", "OFF"])
    def test_falsy_values_are_false(self, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", val)
        assert is_dry_run() is False

    def test_empty_string_is_true_safe_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty-string env var (accidental unset) MUST NOT go live."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", "")
        assert is_dry_run() is True

    @pytest.mark.parametrize("val", ["maybe", "possibly", "enable", "gibberish"])
    def test_unrecognised_values_fail_safe_to_true(
        self, monkeypatch: pytest.MonkeyPatch, val: str
    ) -> None:
        """Typos route to the safe side, not live."""
        monkeypatch.setenv("OUTREACH_DRY_RUN", val)
        assert is_dry_run() is True

    def test_whitespace_is_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_DRY_RUN", "  false  ")
        assert is_dry_run() is False


class TestCannedSendMail:
    """Dry-run canned response for sendMail."""

    def test_shape_matches_real_return(self) -> None:
        result = canned_send_mail(
            user_id="op-1",
            to_address="hm@corp.com",
            subject="Test",
        )
        # All keys the real send_mail produces must be present so the
        # caller code path is identical.
        for key in (
            "graph_message_id",
            "conversation_id",
            "user_id",
            "to_address",
            "subject",
            "sent_at",
            "dry_run",
        ):
            assert key in result

    def test_ids_are_clearly_synthetic(self) -> None:
        """Any DB row or log line should be distinguishable from real at a glance."""
        result = canned_send_mail(user_id="op", to_address="x@y", subject="s")
        assert result["graph_message_id"].startswith("dryrun-msg-")
        assert result["conversation_id"].startswith("dryrun-conv-")
        assert result["dry_run"] is True

    def test_ids_are_unique_per_call(self) -> None:
        """Sequential calls must not collide (uuid4-backed)."""
        a = canned_send_mail(user_id="op", to_address="x@y", subject="s")
        b = canned_send_mail(user_id="op", to_address="x@y", subject="s")
        assert a["graph_message_id"] != b["graph_message_id"]
        assert a["conversation_id"] != b["conversation_id"]

    def test_sent_at_is_iso_utc(self) -> None:
        from datetime import datetime
        result = canned_send_mail(user_id="op", to_address="x@y", subject="s")
        parsed = datetime.fromisoformat(result["sent_at"])
        assert parsed.tzinfo is not None  # timezone-aware


class TestCannedListReplies:
    """Dry-run canned response for list_replies — always empty."""

    def test_always_returns_empty(self) -> None:
        out = canned_list_replies(user_id="op", conversation_id="conv-xyz")
        assert out == []

    def test_does_not_raise_on_weird_inputs(self) -> None:
        # Defensive: callers shouldn't pass empties, but if they do the
        # canned response must still be a list, not an error.
        assert canned_list_replies(user_id="", conversation_id="") == []
