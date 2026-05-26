#!/usr/bin/env bash
# deploy.sh — build, tag, and deploy all services with a version stamp.
# Usage:
#   ./scripts/deploy.sh              # auto version: YYYYMMDD-HHMM
#   ./scripts/deploy.sh 20260525-01  # explicit version
#
# After deploy, 'rollback.sh <version>' can switch to any saved version.

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:-$(date +%Y%m%d-%H%M)}"
VERSIONS_DIR="infra/versions"
mkdir -p "$VERSIONS_DIR"

echo "==> Deploying version: $VERSION"

# Build all images and tag them with the version (--no-cache ensures Go recompiles)
APP_VERSION="$VERSION" sudo -E docker-compose build --no-cache

# Also tag as 'latest'
for svc in brain backend frontend; do
  sudo docker tag "market-ai-${svc}:${VERSION}" "market-ai-${svc}:latest"
done

# Bring up with the new version
APP_VERSION="$VERSION" sudo -E docker-compose up -d

# Save version history
echo "$VERSION  $(date '+%Y-%m-%d %H:%M')  $(git rev-parse --short HEAD 2>/dev/null || echo 'no-git')" >> "$VERSIONS_DIR/history"
echo "$VERSION" > "$VERSIONS_DIR/current"

# Auto-populate notes for ALL versions that don't have one yet (backfill from git SHA)
mkdir -p "$VERSIONS_DIR/notes"
while IFS= read -r line; do
  vtag=$(echo "$line" | awk '{print $1}')
  vsha=$(echo "$line" | awk '{print $4}')
  nfile="$VERSIONS_DIR/notes/$vtag"
  if [ ! -f "$nfile" ] || [ ! -s "$nfile" ]; then
    if [ -n "$vsha" ] && git rev-parse --verify "$vsha" &>/dev/null 2>&1; then
      git log -1 --pretty=format:"%s" "$vsha" 2>/dev/null > "$nfile" || echo "Deploy $vtag" > "$nfile"
    else
      echo "Deploy $vtag" > "$nfile"
    fi
  fi
done < "$VERSIONS_DIR/history"

echo ""
echo "==> Deployed: $VERSION"
echo "==> To roll back: ./scripts/rollback.sh <version>"
echo "==> Version history:"
tail -5 "$VERSIONS_DIR/history"
