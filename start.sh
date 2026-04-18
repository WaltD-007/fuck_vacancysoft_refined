#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Starting Prospero..."

# Kill anything on our ports
lsof -i :8000 -t 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -i :3000 -t 2>/dev/null | xargs kill -9 2>/dev/null || true
pkill -f "arq vacancysoft" 2>/dev/null || true
sleep 1

# Start Redis
if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
    echo "  Redis:    running"
elif command -v brew &>/dev/null; then
    brew services start redis 2>/dev/null || true
    sleep 1
    echo "  Redis:    started"
else
    echo "  Redis:    NOT FOUND — install with: brew install redis"
    exit 1
fi

# Start PostgreSQL
if /opt/homebrew/opt/postgresql@17/bin/pg_isready -q 2>/dev/null; then
    echo "  Postgres: running"
else
    brew services start postgresql@17 2>/dev/null || true
    sleep 2
    echo "  Postgres: started"
fi

# Start API server
python3 -m vacancysoft.api.server &
API_PID=$!
sleep 2
echo "  API:      http://localhost:8000 (pid $API_PID)"

# Start worker
python3 -m arq vacancysoft.worker.settings.WorkerSettings &
WORKER_PID=$!
echo "  Worker:   25 concurrent slots (pid $WORKER_PID)"

# Start frontend
cd web && npm run dev &
WEB_PID=$!
cd ..
sleep 3
echo "  Frontend: http://localhost:3000 (pid $WEB_PID)"

# Open browser
open http://localhost:3000

echo ""
echo "All services running. Press Ctrl+C to stop."

trap "kill $API_PID $WORKER_PID $WEB_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait
