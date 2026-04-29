"""Unit tests for the pure functions in outreach/tracking.py.

No DB, no FastAPI — just the token signing, HTML manipulation, and
detection helpers. Endpoint behaviour is in test_tracking_endpoints.py.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from vacancysoft.outreach import tracking as tk


# ── Tokens ──────────────────────────────────────────────────────────


class TestTokens:

    def test_round_trip_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "test-secret-1")
        token = tk.sign_token("msg-123", "o")
        payload = tk.verify_token(token, expected_type="o")
        assert payload is not None
        assert payload["m"] == "msg-123"
        assert payload["t"] == "o"
        assert "u" not in payload  # opens carry no URL

    def test_round_trip_click_with_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "test-secret-1")
        token = tk.sign_token("msg-123", "c", url="https://example.com/x?y=1")
        payload = tk.verify_token(token, expected_type="c")
        assert payload is not None
        assert payload["m"] == "msg-123"
        assert payload["u"] == "https://example.com/x?y=1"

    def test_wrong_secret_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "issuer")
        token = tk.sign_token("msg-1", "o")
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "verifier")
        assert tk.verify_token(token, expected_type="o") is None

    def test_tampered_payload_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "secret")
        token = tk.sign_token("msg-1", "o")
        # Flip a payload byte (the part before the dot).
        head, tail = token.split(".", 1)
        # Mangle the last char of head.
        mangled = head[:-1] + ("Z" if head[-1] != "Z" else "Y") + "." + tail
        assert tk.verify_token(mangled, expected_type="o") is None

    def test_tampered_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "secret")
        token = tk.sign_token("msg-1", "o")
        head, tail = token.split(".", 1)
        mangled = head + "." + tail[:-1] + ("Z" if tail[-1] != "Z" else "Y")
        assert tk.verify_token(mangled, expected_type="o") is None

    def test_wrong_type_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token signed for opens shouldn't validate as click."""
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "secret")
        token = tk.sign_token("msg-1", "o")
        assert tk.verify_token(token, expected_type="c") is None
        assert tk.verify_token(token, expected_type="o") is not None

    def test_garbage_input_returns_none(self) -> None:
        assert tk.verify_token("") is None
        assert tk.verify_token("not-a-token") is None
        assert tk.verify_token("a.b.c.d") is None  # too many segments
        assert tk.verify_token("....") is None
        assert tk.verify_token(None) is None  # type: ignore[arg-type]


# ── Pixel injection ─────────────────────────────────────────────────


class TestPixelInjection:

    def test_inserts_before_body_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = "<html><body><p>Hi</p></body></html>"
        out = tk.inject_pixel(html, "msg-1", "https://t.example.com")
        # Pixel sits immediately before </body>
        assert "<p>Hi</p><img" in out
        assert "</body></html>" in out
        assert 'src="https://t.example.com/t/o/' in out

    def test_appends_when_no_body_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = "<p>Just a fragment</p>"
        out = tk.inject_pixel(html, "msg-1", "https://t.example.com")
        assert out.startswith(html)
        assert out.endswith('alt="">')

    def test_handles_uppercase_body_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = "<HTML><BODY><P>Hi</P></BODY></HTML>"
        out = tk.inject_pixel(html, "msg-1", "https://t.example.com")
        # Pixel injected before </BODY>
        assert "<img" in out
        assert out.index("<img") < out.index("</BODY>")

    def test_empty_body_just_appends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        out = tk.inject_pixel("", "msg-1", "https://t.example.com")
        assert "<img" in out

    def test_pixel_url_round_trips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token in injected pixel verifies back to the right msg id."""
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = tk.inject_pixel("<body></body>", "msg-XYZ", "https://t.example.com")
        # Extract token between /t/o/ and the closing quote
        start = html.index("/t/o/") + len("/t/o/")
        end = html.index('"', start)
        token = html[start:end]
        payload = tk.verify_token(token, expected_type="o")
        assert payload is not None and payload["m"] == "msg-XYZ"


# ── Link rewriting ──────────────────────────────────────────────────


class TestLinkRewriting:

    def test_rewrites_simple_link(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<p>Click <a href="https://example.com/page">here</a></p>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert 'href="https://t.example.com/t/c/' in out
        assert "https://example.com/page" not in out  # original gone

    def test_preserves_other_attrs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<a target="_blank" href="https://example.com" rel="noopener">x</a>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert 'target="_blank"' in out
        assert 'rel="noopener"' in out

    def test_handles_single_quotes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = "<a href='https://example.com'>x</a>"
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert "https://t.example.com/t/c/" in out

    def test_skips_mailto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<a href="mailto:a@b.com">email</a>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert out == html  # untouched

    def test_skips_tel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<a href="tel:+44123">call</a>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert out == html

    def test_skips_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<a href="#section">jump</a>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert out == html

    def test_idempotent_on_already_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rewriting twice doesn't double-wrap our own /t/c URLs."""
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = '<a href="https://example.com">x</a>'
        once = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        twice = tk.rewrite_links(once, "msg-1", "https://t.example.com")
        assert once == twice

    def test_multiple_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        html = (
            '<a href="https://a.com">A</a> '
            '<a href="https://b.com">B</a> '
            '<a href="mailto:c@d">C</a>'
        )
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        assert out.count("https://t.example.com/t/c/") == 2
        assert "mailto:c@d" in out  # mailto preserved

    def test_rewritten_url_round_trips_to_original(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        original = "https://example.com/very/specific/path?q=1&r=2"
        html = f'<a href="{original}">x</a>'
        out = tk.rewrite_links(html, "msg-1", "https://t.example.com")
        # Extract the token, verify, confirm URL is preserved.
        start = out.index("/t/c/") + len("/t/c/")
        end = out.index('"', start)
        token = out[start:end]
        payload = tk.verify_token(token, expected_type="c")
        assert payload is not None and payload["u"] == original


# ── IP hashing ──────────────────────────────────────────────────────


class TestHashIp:

    def test_hash_is_deterministic_within_secret(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "secret-A")
        a = tk.hash_ip("1.2.3.4")
        b = tk.hash_ip("1.2.3.4")
        assert a == b

    def test_different_ips_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "x")
        assert tk.hash_ip("1.2.3.4") != tk.hash_ip("5.6.7.8")

    def test_secret_rotation_changes_hash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "old")
        old = tk.hash_ip("1.2.3.4")
        monkeypatch.setenv("PROSPERO_TRACKING_SECRET", "new")
        new = tk.hash_ip("1.2.3.4")
        assert old != new

    def test_empty_returns_empty(self) -> None:
        assert tk.hash_ip(None) == ""
        assert tk.hash_ip("") == ""


# ── Heuristics ──────────────────────────────────────────────────────


class TestScannerHeuristic:

    def test_proofpoint_ua_flagged(self) -> None:
        assert tk.is_likely_scanner_ua(
            "Mozilla/5.0 (compatible; ProofPoint URL Defense)"
        )

    def test_mimecast_ua_flagged(self) -> None:
        assert tk.is_likely_scanner_ua("Mimecast Email Security")

    def test_normal_browser_not_flagged(self) -> None:
        assert not tk.is_likely_scanner_ua(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"
        )

    def test_empty_ua_not_flagged(self) -> None:
        assert not tk.is_likely_scanner_ua("")
        assert not tk.is_likely_scanner_ua(None)

    def test_click_within_window_flagged(self) -> None:
        assert tk.is_likely_scanner_click(
            user_agent="Chrome",
            time_since_send=timedelta(seconds=30),
        )

    def test_click_outside_window_not_flagged(self) -> None:
        assert not tk.is_likely_scanner_click(
            user_agent="Chrome",
            time_since_send=timedelta(seconds=300),
        )

    def test_click_with_no_send_time_falls_back_to_ua(self) -> None:
        assert tk.is_likely_scanner_click(
            user_agent="Mimecast", time_since_send=None
        )
        assert not tk.is_likely_scanner_click(
            user_agent="Chrome", time_since_send=None
        )


class TestApplePrefetch:

    def test_google_image_proxy_flagged(self) -> None:
        assert tk.is_likely_apple_mpp_ua("GoogleImageProxy")

    def test_normal_browser_not_flagged(self) -> None:
        assert not tk.is_likely_apple_mpp_ua("Mozilla/5.0 (Mac)")


# ── Kill switch ─────────────────────────────────────────────────────


class TestKillSwitch:

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTREACH_TRACKING_ENABLED", raising=False)
        assert tk.is_tracking_enabled() is True

    def test_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_TRACKING_ENABLED", "false")
        assert tk.is_tracking_enabled() is False

    def test_explicit_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTREACH_TRACKING_ENABLED", "0")
        assert tk.is_tracking_enabled() is False

    def test_typo_treated_as_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Conservative default — anything we don't recognise as falsy
        # is treated as enabled. Matches the OUTREACH_DRY_RUN convention.
        monkeypatch.setenv("OUTREACH_TRACKING_ENABLED", "true")
        assert tk.is_tracking_enabled() is True
        monkeypatch.setenv("OUTREACH_TRACKING_ENABLED", "yes")
        assert tk.is_tracking_enabled() is True
