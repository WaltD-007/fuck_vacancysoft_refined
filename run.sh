#!/usr/bin/env bash
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Move to project root (wherever this script lives) ────────────────────────
cd "$(dirname "$0")"

# ── 1. Python check ──────────────────────────────────────────────────────────
info "Checking Python version..."
python3 --version || error "python3 not found. Install Python 3.12+."

# ── 2. .env check ────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        warn ".env not found — copying .env.example to .env"
        cp .env.example .env
        warn "Open .env and fill in your SERPAPI_KEY before running the pipeline."
    else
        warn "No .env file found. Some adapters may fail without API keys."
    fi
fi

# ── 3. Install package ───────────────────────────────────────────────────────
info "Installing package in editable mode..."
pip install -e . --quiet || error "pip install failed."

# ── 4. Install Playwright browsers (needed for browser-based adapters) ───────
info "Installing Playwright browser binaries..."
python3 -m playwright install chromium || warn "Playwright install failed — browser adapters may not work."

# ── 5. Initialise database ───────────────────────────────────────────────────
info "Initialising database..."
prospero db init

# ── 6. Seed sources ──────────────────────────────────────────────────────────
info "Seeding sources from config..."
prospero db seed-config-boards

info "DB stats after seeding:"
prospero db stats

# ── 7. Run the full pipeline ─────────────────────────────────────────────────
# Any extra flags are passed straight through to `pipeline run`
# e.g.  ./run.sh --adapter greenhouse --discover-limit 5 --dry-run
info "Starting pipeline..."
prospero pipeline run "$@"

info "Done. Check leads_output.xlsx for results."
