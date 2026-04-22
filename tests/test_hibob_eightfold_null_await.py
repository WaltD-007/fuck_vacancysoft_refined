"""Tests for the null-await defensive fix in hibob + eightfold.

Context: the 2026-04-22 full-pipeline run surfaced 28 hibob runs + 2
eightfold runs crashing with ``TypeError: object NoneType can't be
used in 'await' expression`` and NO stack trace in diagnostics_blob.

Fix: (1) `_maybe_await(value)` helper that skips the await when the
value isn't awaitable, protecting against sync callbacks passed as
`on_page_scraped`. (2) top-level `discover` wrapper that captures any
unhandled exception into diagnostics.errors WITH full traceback, so
the DB stores a usable error message next time.

These tests don't run a live scrape — they verify the helper logic
in isolation.
"""

from __future__ import annotations

import pytest

from vacancysoft.adapters.eightfold import _maybe_await as _maybe_await_ef
from vacancysoft.adapters.hibob import _maybe_await as _maybe_await_hb


class TestMaybeAwait:
    """Both adapters expose an identical helper. Test them together."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("helper", [_maybe_await_hb, _maybe_await_ef])
    async def test_awaits_a_coroutine(self, helper) -> None:
        """Classic case — an async callback returns a coroutine. Helper
        must await it (or at least not crash)."""

        called = {"n": 0}

        async def async_cb(n, a, b):
            called["n"] = n
            return None

        await helper(async_cb(1, [], []))
        assert called["n"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("helper", [_maybe_await_hb, _maybe_await_ef])
    async def test_noop_on_none(self, helper) -> None:
        """The bug case — sync callback returned None. Helper must NOT
        crash with TypeError: object NoneType can't be used in 'await'
        expression."""
        # Before the fix: `await None` raises TypeError.
        # After the fix: _maybe_await(None) is a silent no-op.
        await helper(None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("helper", [_maybe_await_hb, _maybe_await_ef])
    async def test_noop_on_plain_value(self, helper) -> None:
        """Sync callback returned a non-None, non-awaitable value. Helper
        must still no-op (don't blow up on stray return values)."""
        await helper(42)
        await helper("a string")
        await helper({"ok": True})
        await helper([1, 2, 3])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("helper", [_maybe_await_hb, _maybe_await_ef])
    async def test_awaits_a_future(self, helper) -> None:
        """asyncio.Future is awaitable — must be awaited."""
        import asyncio
        fut = asyncio.get_event_loop().create_future()
        fut.set_result("done")
        await helper(fut)
        assert fut.result() == "done"


class TestDiscoverWrapperCapturesExceptions:
    """The top-level discover() wrapper re-raises unhandled exceptions
    but first appends them to diagnostics.errors with full traceback.

    Verified via an intentional failure in _discover_impl — mock the
    method to raise, then check the exception propagates AND the
    diagnostics get the trace."""

    @pytest.mark.asyncio
    async def test_hibob_discover_captures_traceback(self, monkeypatch) -> None:
        from vacancysoft.adapters.hibob import HiBobAdapter

        adapter = HiBobAdapter()

        async def _boom(*a, **kw):
            raise TypeError("object NoneType can't be used in 'await' expression")

        monkeypatch.setattr(adapter, "_discover_impl", _boom)

        diagnostics_seen: list = []

        # Use a wrapper around discover that captures the diagnostics
        # object the wrapper constructed. Since the wrapper creates it
        # locally and re-raises, we intercept the AdapterDiagnostics
        # constructor to grab a reference.
        from vacancysoft.adapters import hibob as hb_mod
        orig = hb_mod.AdapterDiagnostics

        def _capture(*a, **kw):
            d = orig(*a, **kw)
            diagnostics_seen.append(d)
            return d

        monkeypatch.setattr(hb_mod, "AdapterDiagnostics", _capture)

        with pytest.raises(TypeError, match="NoneType can't be used in 'await'"):
            await adapter.discover({"job_board_url": "https://example.com"})

        assert len(diagnostics_seen) >= 1
        d = diagnostics_seen[0]
        assert any("TypeError" in e for e in d.errors), (
            f"Expected TypeError in diagnostics.errors; got {d.errors!r}"
        )
        assert any("NoneType can't be used in 'await'" in e for e in d.errors)
        # Traceback should name the adapter file so the next occurrence
        # can be pinpointed in the DB.
        assert any("hibob.py" in e or "Traceback" in e for e in d.errors)

    @pytest.mark.asyncio
    async def test_eightfold_discover_captures_traceback(self, monkeypatch) -> None:
        from vacancysoft.adapters.eightfold import EightfoldAdapter

        adapter = EightfoldAdapter()

        async def _boom(*a, **kw):
            raise TypeError("object NoneType can't be used in 'await' expression")

        monkeypatch.setattr(adapter, "_discover_impl", _boom)

        from vacancysoft.adapters import eightfold as ef_mod
        orig = ef_mod.AdapterDiagnostics
        diagnostics_seen: list = []

        def _capture(*a, **kw):
            d = orig(*a, **kw)
            diagnostics_seen.append(d)
            return d

        monkeypatch.setattr(ef_mod, "AdapterDiagnostics", _capture)

        with pytest.raises(TypeError, match="NoneType can't be used in 'await'"):
            await adapter.discover({"job_board_url": "https://example.com"})

        assert diagnostics_seen
        d = diagnostics_seen[0]
        assert any("TypeError" in e for e in d.errors)
        assert any("NoneType can't be used in 'await'" in e for e in d.errors)
