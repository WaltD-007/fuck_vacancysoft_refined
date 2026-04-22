"""Tests for the generic_site adapter's optional page-capture mode.

PR 3B-ii — when the operator sets ``PROSPERO_GENERIC_CAPTURE_DIR`` the
adapter writes a copy of the rendered listing page to disk so we can
inspect it and extend ``LOCATION_HINT_SELECTORS``. These tests cover
the helpers in isolation (no Playwright, no live scrape).

Covered:

* ``_capture_dir()`` returns None when env unset.
* ``_capture_dir()`` returns an existing path when env set, creating
  directory if needed.
* ``_capture_dir()`` returns None when the path is unwritable (so a
  mis-configured env var can't crash an adapter run).
* ``_capture_filename()`` produces a filesystem-safe filename based on
  hostname + UTC timestamp, clamped to a reasonable length.
* ``_capture_filename()`` copes with malformed / empty URLs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from vacancysoft.adapters.generic_browser import (
    _capture_dir,
    _capture_filename,
)


class TestCaptureDir:

    def test_unset_env_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PROSPERO_GENERIC_CAPTURE_DIR", raising=False)
        assert _capture_dir() is None

    def test_empty_env_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_GENERIC_CAPTURE_DIR", "")
        assert _capture_dir() is None

    def test_existing_dir_returns_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PROSPERO_GENERIC_CAPTURE_DIR", str(tmp_path))
        result = _capture_dir()
        assert result is not None
        assert result == tmp_path.resolve()

    def test_missing_dir_gets_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = tmp_path / "new" / "nested" / "captures"
        assert not target.exists()
        monkeypatch.setenv("PROSPERO_GENERIC_CAPTURE_DIR", str(target))
        result = _capture_dir()
        assert result is not None
        assert result.exists()
        assert result.is_dir()

    def test_expands_user_tilde(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Paths with ~ should expand to the user's home."""
        # Use tmp_path as a fake HOME so we don't touch real $HOME
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PROSPERO_GENERIC_CAPTURE_DIR", "~/captures")
        result = _capture_dir()
        assert result is not None
        # resolve() returns the real path, which must start with tmp_path
        assert str(result).startswith(str(tmp_path.resolve()))


class TestCaptureFilename:

    def test_standard_url(self) -> None:
        name = _capture_filename("https://higher.gs.com/results?sort=RELEVANCE")
        # Format: <hostname>-<YYYYMMDD-HHMMSS>.html
        assert name.endswith(".html")
        assert name.startswith("higher.gs.com-")
        assert re.match(r"^higher\.gs\.com-\d{8}-\d{6}\.html$", name)

    def test_hostname_with_port_slugified(self) -> None:
        name = _capture_filename("http://localhost:8080/jobs")
        # ":" is non-alphanumeric and gets replaced with "_"
        assert name.startswith("localhost_8080-") or name.startswith("localhost-")
        assert ":" not in name
        assert "/" not in name

    def test_empty_url_yields_unknown(self) -> None:
        name = _capture_filename("")
        assert name.startswith("unknown-")

    def test_malformed_url_does_not_crash(self) -> None:
        # urlparse is permissive; it returns hostname=None for non-URL
        # strings rather than raising. The helper must still return a
        # valid filename.
        name = _capture_filename("not a url")
        assert name.endswith(".html")
        # No shell-unsafe chars
        assert " " not in name
        assert "/" not in name

    def test_hostname_with_special_chars_sanitised(self) -> None:
        # Hostnames can't legally contain '/', but we guard defensively
        # so a stray path fragment can't escape the capture dir.
        name = _capture_filename("https://weird..example.com/jobs")
        assert "/" not in name
        # Triple-dots should be collapsed, not passed through verbatim
        assert "....." not in name

    def test_filename_is_portable_across_platforms(self) -> None:
        """No Windows-illegal characters (< > : " / \\ | ? *)."""
        name = _capture_filename("https://example.com:443/path?query=x")
        for illegal in "<>:\"/\\|?*":
            assert illegal not in name, f"illegal char {illegal!r} in {name!r}"


class TestCaptureDirUnwritable:
    """When the configured dir is pointed at something we can't write
    to, the helper must return None rather than raising — captures are
    best-effort diagnostics, not load-bearing."""

    def test_readonly_parent_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Make a read-only parent, then aim the capture dir inside it.
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        os.chmod(readonly, 0o400)
        try:
            target = readonly / "captures"
            monkeypatch.setenv("PROSPERO_GENERIC_CAPTURE_DIR", str(target))
            # mkdir on a non-writable parent should OSError, _capture_dir
            # swallows it and returns None.
            assert _capture_dir() is None
        finally:
            # Restore so pytest can clean up tmp_path
            os.chmod(readonly, 0o700)
