#!/usr/bin/env bash
# watchdog.sh — Dead-man's switch for the MarketFlow AI brain (Oracle host).
#
# Cron: every 10 min during market window (13-21 UTC, Mon-Fri).
# Checks:
#   1. brain heartbeat file fresh (< 15 min)
#   2. backend API answering
#   3. brain container running
# On failure: posts a HIGH alert to the dashboard + emails via Resend
# (email throttled to once per hour per check to avoid spam).
#
# This is the alert that would have caught the 2026-07-13 silent stall.
set -u

REPO="${MARKETFLOW_REPO:-/home/ubuntu/Market-AI}"
HB="$REPO/logs/brain_heartbeat.json"
BACKEND="http://127.0.0.1:8080"
MAX_AGE=900                       # 15 min
THROTTLE_DIR="/tmp/marketflow_watchdog"
mkdir -p "$THROTTLE_DIR"

# Pull keys from .env without exporting everything
resend_key=$(grep -E '^RESEND_API_KEY=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)
alert_to=$(grep -E '^ALERT_EMAIL_TO=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)
alert_to="${alert_to:-wrcron1@gmail.com}"

alert() {  # $1=check-name  $2=message
    local name="$1" msg="$2" now last
    echo "WATCHDOG ALERT [$name]: $msg"
    # Dashboard (no throttle — the panel dedupes visually)
    curl -s -m 5 -X POST "$BACKEND/api/alerts" \
        -H 'Content-Type: application/json' \
        -d "{\"severity\":\"HIGH\",\"title\":\"Watchdog: $name\",\"body\":\"$msg\"}" >/dev/null 2>&1
    # Email (1/hour per check)
    now=$(date +%s); last=$(cat "$THROTTLE_DIR/$name" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge 3600 ] && [ -n "$resend_key" ]; then
        echo "$now" > "$THROTTLE_DIR/$name"
        curl -s -m 10 -X POST https://api.resend.com/emails \
            -H "Authorization: Bearer $resend_key" \
            -H 'Content-Type: application/json' \
            -d "{\"from\":\"MarketFlow AI <onboarding@resend.dev>\",\"to\":[\"$alert_to\"],\"subject\":\"🟠 [HIGH] MarketFlow AI — Watchdog: $name\",\"html\":\"<pre>$msg</pre>\"}" >/dev/null 2>&1
    fi
}

failures=0

# 1. Heartbeat freshness
if [ ! -f "$HB" ]; then
    alert "heartbeat-missing" "Brain heartbeat file not found at $HB — brain may be down or volume not mounted."
    failures=$((failures+1))
else
    age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
    if [ "$age" -gt "$MAX_AGE" ]; then
        alert "heartbeat-stale" "Brain heartbeat is ${age}s old (max ${MAX_AGE}s). The brain loop has stalled — check: sudo docker logs market-ai-brain-1 --tail 50"
        failures=$((failures+1))
    fi
fi

# 2. Backend API
if ! curl -s -m 8 -o /dev/null -w '' "$BACKEND/api/stats"; then
    alert "backend-down" "Go backend not answering on :8080 — dashboard and order staging are down."
    failures=$((failures+1))
fi

# 3. Brain container running
running=$(sudo docker inspect -f '{{.State.Running}}' market-ai-brain-1 2>/dev/null || echo "missing")
if [ "$running" != "true" ]; then
    alert "brain-container" "Brain container not running (state: $running). Restart: cd $REPO && sudo docker-compose up -d brain"
    failures=$((failures+1))
fi

if [ "$failures" -eq 0 ]; then
    echo "watchdog OK $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
fi
exit 0
