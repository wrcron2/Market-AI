#!/usr/bin/env bash
# install_cron.sh — Install the MarketFlow AI monitoring cron jobs (idempotent).
# Run once on the Oracle host: bash /home/ubuntu/Market-AI/scripts/oracle/install_cron.sh
set -eu

REPO="${MARKETFLOW_REPO:-/home/ubuntu/Market-AI}"
MARKER="# marketflow-monitoring"

# Preflight: 11:00 UTC (= 7:00am ET) Mon-Fri — 2.5h before market open.
PREFLIGHT="0 11 * * 1-5 /usr/bin/python3 $REPO/scripts/oracle/preflight.py --repo $REPO >> $REPO/logs/preflight.log 2>&1 $MARKER"
# Watchdog: every 10 min, 13-21 UTC Mon-Fri (covers pre/market/post windows).
WATCHDOG="*/10 13-21 * * 1-5 /bin/bash $REPO/scripts/oracle/watchdog.sh >> $REPO/logs/watchdog.log 2>&1 $MARKER"

current=$(crontab -l 2>/dev/null | grep -v "$MARKER" || true)
printf '%s\n%s\n%s\n' "$current" "$PREFLIGHT" "$WATCHDOG" | sed '/^$/d' | crontab -

echo "Installed cron jobs:"
crontab -l | grep "$MARKER"
