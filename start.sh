#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "📁 Project: $PROJECT_DIR"

# ── Check Go ──────────────────────────────────────────────────────────────────
if ! command -v go &>/dev/null; then
  echo "❌ Go not found. Installing via Homebrew..."
  brew install go || { echo "Install Go from https://go.dev/dl/"; exit 1; }
fi
echo "✅ Go $(go version | awk '{print $3}')"

# ── Kill stale processes on our ports ─────────────────────────────────────────
echo ""
echo "🧹 Clearing ports 8080, 50051, 3000..."
for PORT in 8080 50051 3000; do
  PID=$(lsof -ti tcp:$PORT 2>/dev/null || true)
  if [ -n "$PID" ]; then
    echo "   Killing PID $PID on :$PORT"
    kill -9 $PID 2>/dev/null || true
  fi
done
sleep 1

# ── Create DB dir ─────────────────────────────────────────────────────────────
mkdir -p "$PROJECT_DIR/infra/db"

# ── Start Go backend ──────────────────────────────────────────────────────────
echo ""
echo "🚀 Starting Go backend on :8080 ..."
cd "$PROJECT_DIR/backend"
go run cmd/server/main.go &> /tmp/marketflow-backend.log &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID  (logs → /tmp/marketflow-backend.log)"

echo "   Waiting for backend..."
for i in {1..30}; do
  if curl -sf http://localhost:8080/healthz &>/dev/null; then
    echo "✅ Backend is up!"
    break
  fi
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend crashed! Last log lines:"
    tail -20 /tmp/marketflow-backend.log
    exit 1
  fi
  sleep 1
done

# ── Start Frontend ────────────────────────────────────────────────────────────
echo ""
echo "🌐 Starting React frontend on :3000 ..."
cd "$PROJECT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

sleep 3

echo ""
echo "============================================"
echo "  ⚡ MarketFlow AI is running!"
echo "  📊 Dashboard → http://localhost:3000"
echo "  🔧 Backend   → http://localhost:8080"
echo "============================================"
echo "Press Ctrl+C to stop everything."

trap "echo 'Shutting down...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT
wait
