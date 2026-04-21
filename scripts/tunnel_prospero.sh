#!/usr/bin/env bash
#
# One-command Prospero review tunnel.
#
#   scripts/tunnel_prospero.sh [--auth <passphrase>] [--no-worker]
#
# Starts (only the ones that aren't already running): FastAPI on :8000,
# ARQ worker, Next.js on :3000, ngrok on :3000 with HTTP basic auth.
# Prints the public URL + credentials at the end so you can copy-paste
# straight to your colleague.
#
# Defaults:
#   --auth        random 16-char passphrase (printed to you, not logged)
#   --no-worker   if passed, skip the ARQ worker (read-only demos)
#
# Override via env: PROSPERO_TUNNEL_AUTH=<passphrase> scripts/tunnel_prospero.sh
#
# Kill with one Ctrl-C: the script's trap cleans up all child processes
# (anything it started; anything you already had running is left alone).
# The ngrok URL dies the instant ngrok's process does — no cleanup
# action needed on your side beyond Ctrl-C.
#
# Never exposed:
#   - Postgres (:5432)         — bound to localhost by your OS
#   - Redis    (:6379)         — bound to localhost by your OS
#   - FastAPI  (:8000)         — script binds to 127.0.0.1 only; Next.js
#                                server-side rewrite reaches it in-proc
#
# Only :3000 (Next.js) is tunnelled. See docs/local_review_tunnel.md for
# the full runbook, security context, and long-term alternatives.

set -euo pipefail

# ── Resolve repo root (script lives in scripts/; repo is parent) ─────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_ROOT/.data/tunnel_logs"
mkdir -p "$LOG_DIR"

# ── Args ──────────────────────────────────────────────────────────────
AUTH="${PROSPERO_TUNNEL_AUTH:-}"
START_WORKER=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auth)      AUTH="$2"; shift 2 ;;
        --no-worker) START_WORKER=0; shift ;;
        -h|--help)
            sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$AUTH" ]]; then
    # Random 16-char passphrase — alphanumeric to avoid shell-quoting issues.
    AUTH="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"
fi

# ── Prereq checks ─────────────────────────────────────────────────────
need() { command -v "$1" >/dev/null || { echo "✗ missing: $1" >&2; exit 1; }; }
need uvicorn
need arq
need npm
need ngrok
need curl

# ── Service helpers ───────────────────────────────────────────────────
PIDS=()
LABELS=()

is_port_listening() { lsof -iTCP:"$1" -sTCP:LISTEN -n -P 2>/dev/null | grep -q LISTEN; }

start_bg() {
    local label="$1"; shift
    local logfile="$LOG_DIR/$label.log"
    (
        cd "$2"; shift 2
        exec "$@" >"$logfile" 2>&1
    ) &
    local pid=$!
    PIDS+=("$pid")
    LABELS+=("$label")
    printf "  ▶ started %-10s pid=%-6d log=%s\n" "$label" "$pid" "$logfile"
}

cleanup() {
    echo ""
    echo "── Shutting down services started by this script ──"
    for i in "${!PIDS[@]}"; do
        local pid="${PIDS[$i]}"
        local label="${LABELS[$i]}"
        if kill -0 "$pid" 2>/dev/null; then
            printf "  ■ stopping %-10s pid=%d\n" "$label" "$pid"
            kill "$pid" 2>/dev/null || true
        fi
    done
    # give them 2s, then SIGKILL any stragglers
    sleep 2
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    echo "Done. ngrok URL is dead; local state unchanged."
}
trap cleanup EXIT INT TERM

# ── Header ────────────────────────────────────────────────────────────
echo "=================================================================="
echo " Prospero review tunnel"
echo " Repo: $REPO_ROOT"
echo "=================================================================="
echo ""

# ── Start each service iff its port is free (respect already-running) ─
if is_port_listening 8000; then
    echo "  ✓ FastAPI already listening on :8000 — leaving it alone"
else
    start_bg "fastapi" "$REPO_ROOT" \
        uvicorn vacancysoft.api.main:app --host 127.0.0.1 --port 8000 --reload
fi

if [[ "$START_WORKER" == "1" ]]; then
    # The worker doesn't bind a port, so we can't auto-detect whether
    # one is already running. Heuristic: check for an arq process.
    if pgrep -f "arq vacancysoft.worker" >/dev/null; then
        echo "  ✓ ARQ worker already running — leaving it alone"
    else
        start_bg "worker" "$REPO_ROOT" \
            arq vacancysoft.worker.settings.WorkerSettings
    fi
else
    echo "  — skipping ARQ worker (--no-worker)"
fi

if is_port_listening 3000; then
    echo "  ✓ Next.js already listening on :3000 — leaving it alone"
else
    start_bg "nextjs" "$REPO_ROOT/web" \
        npm run dev
fi

# ── Wait for :3000 to answer before starting ngrok ──
echo ""
echo "── Waiting for Next.js on :3000 to be ready ──"
for i in {1..60}; do
    if curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/ | grep -qE '^(200|307|308)$'; then
        echo "  ✓ Next.js is up"
        break
    fi
    sleep 1
    if [[ "$i" == "60" ]]; then
        echo "✗ Next.js didn't respond within 60s — check $LOG_DIR/nextjs.log" >&2
        exit 1
    fi
done

# ── ngrok — always started by us (a second ngrok on same port would fail) ──
start_bg "ngrok" "$REPO_ROOT" \
    ngrok http 3000 --basic-auth "reviewer:$AUTH" --log stdout

# ── Poll ngrok's local admin API for the public URL ──
echo ""
echo "── Waiting for ngrok to publish a public URL ──"
PUBLIC_URL=""
for i in {1..30}; do
    PUBLIC_URL="$(curl -sS http://127.0.0.1:4040/api/tunnels 2>/dev/null \
        | grep -oE '"public_url":"https://[^"]+' | head -n1 | sed 's|"public_url":"||')"
    if [[ -n "$PUBLIC_URL" ]]; then
        break
    fi
    sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
    echo "✗ ngrok didn't publish a URL within 30s — check $LOG_DIR/ngrok.log" >&2
    exit 1
fi

# ── The share block ───────────────────────────────────────────────────
echo ""
echo "=================================================================="
echo " Prospero is live behind basic auth"
echo "=================================================================="
echo ""
echo "   URL:       $PUBLIC_URL"
echo "   User:      reviewer"
echo "   Password:  $AUTH"
echo ""
echo "Send those three lines to your colleague."
echo ""
echo "ngrok admin dashboard: http://localhost:4040"
echo "Logs (tail while running):"
for label in "${LABELS[@]}"; do
    echo "   tail -f $LOG_DIR/$label.log"
done
echo ""
echo "Press Ctrl-C to tear everything (this script started) down."
echo "=================================================================="
echo ""

# ── Block until the operator presses Ctrl-C ──
# Wait on any PID; the trap handles cleanup.
wait
