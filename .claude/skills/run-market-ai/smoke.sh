#!/bin/bash
# MarketFlow AI smoke driver — launches backend + frontend from source and
# verifies them end-to-end without touching a developer's running instances
# (scratch ports 8090/50061/3100, throwaway SQLite DB).
#
# Usage:  .claude/skills/run-market-ai/smoke.sh [workdir]
#   workdir: where to put binary/db/logs/screenshot (default: mktemp -d)
# Exit 0 = all checks passed. Screenshot lands at $WORK/dashboard.png.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PASS=0; FAIL=0
BACKEND_PID=""; VITE_PID=""

check() { # check <name> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "PASS  $1 ($3)"; PASS=$((PASS+1));
  else echo "FAIL  $1 (expected $2, got $3)"; FAIL=$((FAIL+1)); fi
}
cleanup() {
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null
}
trap cleanup EXIT

echo "== workdir: $WORK"

# ── 1. Backend: build + launch on scratch ports with a fresh DB ──────────────
(cd "$ROOT/backend" && go build -o "$WORK/mf-server" ./cmd/server) || { echo "FAIL backend build"; exit 1; }
echo "PASS  backend build"
GO_SERVER_PORT=8090 GO_GRPC_PORT=50061 DB_DSN="$WORK/smoke.db" \
  "$WORK/mf-server" > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
sleep 2
check "backend /api/orders/pending" 200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8090/api/orders/pending)"
check "backend /api/mode"           200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8090/api/mode)"
check "backend /api/auto-execute"   200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8090/api/auto-execute)"
# NOTE: /api/stats 500s on an empty DB (aggregate over zero rows) — not a probe.

# ── 2. Frontend: vite dev server on scratch port ─────────────────────────────
(cd "$ROOT/frontend" && npx vite --port 3100 --strictPort > "$WORK/vite.log" 2>&1) &
VITE_PID=$!
sleep 3
check "frontend root"              200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/)"
check "frontend deep route"        200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/portfolio)"
# Proxy targets localhost:8080 (vite.config.ts) — only meaningful if a dev
# backend is running there; report but don't fail the suite on it.
if curl -s -o /dev/null --max-time 2 http://localhost:8080/api/orders/pending; then
  check "frontend →8080 api proxy" 200 "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/api/orders/pending)"
else
  echo "SKIP  frontend api proxy (nothing on :8080)"
fi

# ── 3. Screenshot (headless Chrome) ──────────────────────────────────────────
if [ -x "$CHROME" ]; then
  "$CHROME" --headless --disable-gpu --window-size=1440,900 \
    --virtual-time-budget=8000 --screenshot="$WORK/dashboard.png" \
    http://localhost:3100/portfolio > /dev/null 2>&1
  [ -s "$WORK/dashboard.png" ] && { echo "PASS  screenshot → $WORK/dashboard.png"; PASS=$((PASS+1)); } \
                               || { echo "FAIL  screenshot empty"; FAIL=$((FAIL+1)); }
else
  echo "SKIP  screenshot (Chrome not found)"
fi

# ── 4. Brain: import check (full pipeline needs Ollama + backend, hours) ─────
if (cd "$ROOT/ai-brain" && python3 -c "import main" 2>/dev/null); then
  echo "PASS  brain imports"; PASS=$((PASS+1))
else
  echo "FAIL  brain imports (pip install -r ai-brain/requirements.txt)"; FAIL=$((FAIL+1))
fi

echo "== $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
