#!/usr/bin/env python3
"""launch_grok.py — one-command Prospero review tunnel bringup.

Run from the repo root:

    python3 launch_grok.py                       # default: random password, dev Next.js
    python3 launch_grok.py --auth my-demo-pw     # fixed password
    python3 launch_grok.py --prod                # production Next.js build (faster)
    python3 launch_grok.py --no-worker           # skip ARQ worker
    python3 launch_grok.py --help

Starts (only the ones not already running):
  - FastAPI on 127.0.0.1:8000
  - ARQ worker
  - Next.js on :3000 (dev mode by default; --prod to build + serve)
  - ngrok tunnel on :3000 with basic auth

Then prints a paste-ready share block: URL + reviewer username + password.

Ctrl-C tears down everything the script started. Services that were
already running before the script started are left alone.

Prerequisites (script errors clearly if any are missing):
  - Postgres listening on 127.0.0.1:5432
  - Redis listening on 127.0.0.1:6379
  - `npm`, `ngrok` on PATH
  - `uvicorn` and `arq` on PATH OR importable as Python modules

Never exposed externally:
  - Postgres (:5432), Redis (:6379), FastAPI (:8000) all stay on loopback.
  - Only :3000 (Next.js) is tunnelled by ngrok.

See docs/local_review_tunnel.md for the full runbook and security context.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / ".data" / "tunnel_logs"


# ── Tiny helpers ─────────────────────────────────────────────────────


def port_in_use(port: int) -> bool:
    """True if *something* is listening on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pgrep_running(pattern: str) -> bool:
    """True if any process matches the pattern (uses /usr/bin/pgrep)."""
    try:
        subprocess.check_output(
            ["pgrep", "-f", pattern], stderr=subprocess.DEVNULL
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def find_launcher(binary: str, module: str | None) -> list[str]:
    """Return the argv prefix for launching ``binary``.

    Prefers the binary on PATH; falls back to ``python3 -m <module>`` if
    the module imports successfully. Fails with an actionable error
    otherwise.
    """
    if shutil.which(binary):
        return [binary]
    if module:
        try:
            subprocess.check_output(
                ["python3", "-c", f"import {module}"],
                stderr=subprocess.DEVNULL,
            )
            return ["python3", "-m", module]
        except subprocess.CalledProcessError:
            pass
    print(f"✗ missing: {binary}", file=sys.stderr)
    if module:
        print(
            f"  also tried `python3 -m {module}` but the module won't import",
            file=sys.stderr,
        )
    print(f"  fix: `pip install -e '.[dev]'` in {ROOT}", file=sys.stderr)
    sys.exit(1)


# ── Service tracking ─────────────────────────────────────────────────

# Tuples of (label, Popen, open-log-file) for every service this script
# started. Services that were already running before the script ran are
# NOT in here, so cleanup never touches them.
procs: list[tuple[str, subprocess.Popen, object]] = []


def spawn(label: str, cwd: Path, cmd: list[str]) -> subprocess.Popen:
    """Start ``cmd`` in the background with stdout/stderr → per-service log."""
    log_path = LOG_DIR / f"{label}.log"
    log = open(log_path, "w")
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # isolate from parent's signal forwarding
    )
    procs.append((label, p, log))
    print(f"  ▶ started {label:10s} pid={p.pid:<6d} log={log_path}")
    return p


def assert_alive(label: str, p: subprocess.Popen, wait: float = 1.5) -> None:
    """Wait ``wait`` seconds and confirm the process is still alive. If
    it died, dump the last 20 lines of its log to stderr + exit 1."""
    time.sleep(wait)
    if p.poll() is not None:
        log_path = LOG_DIR / f"{label}.log"
        print(
            f"\n✗ {label} died on startup (exit code {p.returncode}).",
            file=sys.stderr,
        )
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            print("Last lines of log:", file=sys.stderr)
            for line in tail:
                print(f"  {line}", file=sys.stderr)
        except Exception:
            print(f"(couldn't read {log_path})", file=sys.stderr)
        sys.exit(1)


def wait_for_port(port: int, timeout: int = 60, label: str = "") -> bool:
    """Block until :port is reachable, or timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if port_in_use(port):
            return True
        time.sleep(1)
    if label:
        print(
            f"✗ {label}: port {port} didn't come up within {timeout}s",
            file=sys.stderr,
        )
    return False


def fetch_ngrok_url(timeout: int = 30) -> str | None:
    """Poll ngrok's admin API for the public URL."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:4040/api/tunnels", timeout=2
            ) as r:
                data = json.loads(r.read())
                for tunnel in data.get("tunnels", []):
                    url = tunnel.get("public_url", "")
                    if url.startswith("https://"):
                        return url
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    return None


def cleanup() -> None:
    """Stop every service this script started. Services that were
    already running when we launched are left alone."""
    if not procs:
        return
    print("\n── Shutting down services started by this script ──")
    # SIGTERM first
    for label, p, log in procs:
        if p.poll() is None:
            print(f"  ■ stopping {label:10s} pid={p.pid}")
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            log.close()
        except Exception:
            pass
    # Give them 2s, then SIGKILL any stragglers
    time.sleep(2)
    for label, p, _ in procs:
        if p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    print("Done. ngrok URL is dead; local state unchanged.")


# ── main ─────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else "",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument(
        "--auth",
        default=os.environ.get("PROSPERO_TUNNEL_AUTH", ""),
        help="Fixed basic-auth password (default: random 16 hex chars)",
    )
    ap.add_argument(
        "--no-worker",
        action="store_true",
        help="Skip the ARQ worker (read-only demos)",
    )
    ap.add_argument(
        "--prod",
        action="store_true",
        help="Build + serve Next.js in production mode (faster over the tunnel)",
    )
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    auth = args.auth or secrets.token_hex(8)

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    print("=" * 66)
    print(" Prospero review tunnel")
    print(f" Repo: {ROOT}")
    print("=" * 66)
    print()

    # ── Prereqs ─────────────────────────────────────────────────────
    for binary in ("npm", "ngrok"):
        if not shutil.which(binary):
            print(f"✗ missing: {binary}", file=sys.stderr)
            print(f"  fix: `brew install {binary}`", file=sys.stderr)
            return 1
    uvicorn_cmd = find_launcher("uvicorn", "uvicorn")
    arq_cmd = find_launcher("arq", "arq")

    if not port_in_use(5432):
        print("✗ Postgres not reachable on 127.0.0.1:5432", file=sys.stderr)
        print("  fix: `brew services start postgresql@17`", file=sys.stderr)
        return 1
    if not port_in_use(6379):
        print("✗ Redis not reachable on 127.0.0.1:6379", file=sys.stderr)
        print("  fix: `brew services start redis`", file=sys.stderr)
        return 1
    print("  ✓ Postgres and Redis reachable")

    # ── FastAPI ─────────────────────────────────────────────────────
    if port_in_use(8000):
        print("  ✓ FastAPI already listening on :8000 — leaving it alone")
    else:
        p = spawn(
            "fastapi",
            ROOT,
            uvicorn_cmd
            + [
                "vacancysoft.api.main:app",
                "--host", "127.0.0.1",
                "--port", "8000",
                "--reload",
            ],
        )
        # Bail loudly on immediate crash
        assert_alive("fastapi", p, wait=1.5)
        if not wait_for_port(8000, timeout=30, label="fastapi"):
            assert_alive("fastapi", p, wait=0)
            return 1
        print("  ✓ FastAPI is up")

    # ── ARQ worker ──────────────────────────────────────────────────
    if args.no_worker:
        print("  — skipping ARQ worker (--no-worker)")
    elif pgrep_running("arq vacancysoft.worker"):
        print("  ✓ ARQ worker already running — leaving it alone")
    else:
        p = spawn(
            "worker",
            ROOT,
            arq_cmd + ["vacancysoft.worker.settings.WorkerSettings"],
        )
        assert_alive("worker", p, wait=2.5)

    # ── Next.js ─────────────────────────────────────────────────────
    web_dir = ROOT / "web"
    if port_in_use(3000):
        print("  ✓ Next.js already listening on :3000 — leaving it alone")
    else:
        if args.prod:
            print("── Building Next.js production assets (this takes ~30s) ──")
            rc = subprocess.call(["npm", "run", "build"], cwd=str(web_dir))
            if rc != 0:
                print("✗ `npm run build` failed. Fix the build then retry.", file=sys.stderr)
                return 1
            p = spawn("nextjs", web_dir, ["npm", "start"])
        else:
            p = spawn("nextjs", web_dir, ["npm", "run", "dev"])
        if not wait_for_port(3000, timeout=90, label="nextjs"):
            assert_alive("nextjs", p, wait=0)
            return 1
        print("  ✓ Next.js is up")

    # ── ngrok (always started by us) ───────────────────────────────
    print()
    print("── Starting ngrok tunnel ──")
    p = spawn(
        "ngrok",
        ROOT,
        [
            "ngrok", "http", "3000",
            "--basic-auth", f"reviewer:{auth}",
            "--log", "stdout",
        ],
    )
    assert_alive("ngrok", p, wait=2.5)

    url = fetch_ngrok_url(timeout=30)
    if not url:
        print(
            "\n✗ ngrok didn't publish a public URL within 30s.\n"
            f"  Check {LOG_DIR}/ngrok.log — common causes:\n"
            "    1. Authtoken not set: `ngrok config add-authtoken <token>`\n"
            "    2. Authtoken invalid or revoked",
            file=sys.stderr,
        )
        return 1

    # ── The share block ────────────────────────────────────────────
    bar = "=" * 66
    print()
    print(bar)
    print(" Prospero is live behind basic auth")
    print(bar)
    print()
    print(f"   URL:       {url}")
    print("   User:      reviewer")
    print(f"   Password:  {auth}")
    print()
    print(" Send those three lines to your colleague.")
    print()
    print(" ngrok admin dashboard: http://127.0.0.1:4040")
    print(f" Logs:                   {LOG_DIR}")
    print()
    print(" Press Ctrl-C to tear everything (this script started) down.")
    print(bar)

    # ── Wait forever; surface child deaths ─────────────────────────
    try:
        while True:
            for label, p, _ in procs:
                if p.poll() is not None:
                    print(
                        f"\n✗ {label} exited unexpectedly (code {p.returncode}); tearing down",
                        file=sys.stderr,
                    )
                    return 1
            time.sleep(3)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
