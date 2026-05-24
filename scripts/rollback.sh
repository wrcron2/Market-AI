#!/usr/bin/env bash
# rollback.sh — switch all running containers to a previously built version.
# Usage:
#   ./scripts/rollback.sh 20260524-1430   # roll back to specific version
#   ./scripts/rollback.sh --list          # list available versions
#
# No rebuild needed — uses the already-built image from that version.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--list" ]]; then
  echo "==> Available versions:"
  if [[ -f infra/versions/history ]]; then
    cat infra/versions/history
  else
    echo "  (no version history found)"
  fi
  echo ""
  echo "==> Currently running:"
  cat infra/versions/current 2>/dev/null || echo "  (unknown)"
  exit 0
fi

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <version>   or   $0 --list"
  exit 1
fi

# Verify the images exist locally for that version
MISSING=()
for svc in brain backend frontend; do
  if ! sudo docker image inspect "market-ai-${svc}:${TARGET}" &>/dev/null; then
    MISSING+=("market-ai-${svc}:${TARGET}")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: These images don't exist for version '$TARGET':"
  for img in "${MISSING[@]}"; do echo "  $img"; done
  echo ""
  echo "Available versions:"
  cat .version-history 2>/dev/null || echo "  (no history)"
  exit 1
fi

VERSIONS_DIR="infra/versions"
CURRENT=$(cat "$VERSIONS_DIR/current" 2>/dev/null || cat .current-version 2>/dev/null || echo "unknown")
echo "==> Switching: $CURRENT → $TARGET"

APP_VERSION="$TARGET" sudo -E docker-compose up -d --no-build

echo "$TARGET" > "$VERSIONS_DIR/current"
rm -f "$VERSIONS_DIR/switch-request"
echo "==> Switch complete. Running version: $TARGET"
