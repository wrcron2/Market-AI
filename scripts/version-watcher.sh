#!/usr/bin/env bash
# version-watcher.sh — watches infra/versions/switch-request and triggers rollback.
# Runs as a systemd service on the Oracle host (not in Docker).
# When the backend writes a version to switch-request, this picks it up.

set -euo pipefail
MARKET_AI_DIR="/home/ubuntu/Market-AI"
SWITCH_FILE="$MARKET_AI_DIR/infra/versions/switch-request"

echo "[version-watcher] started, watching $SWITCH_FILE"

while true; do
  if [[ -f "$SWITCH_FILE" && -s "$SWITCH_FILE" ]]; then
    TARGET=$(cat "$SWITCH_FILE" | tr -d '[:space:]')
    echo "[version-watcher] switch request: $TARGET"
    rm -f "$SWITCH_FILE"
    cd "$MARKET_AI_DIR"
    bash scripts/rollback.sh "$TARGET" || echo "[version-watcher] rollback failed for $TARGET"
  fi
  sleep 3
done
