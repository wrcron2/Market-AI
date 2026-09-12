#!/bin/bash
# mac-control host agent manager: start | stop | status
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DIR")"
TOKEN_FILE="$ROOT/.agent-token"
PIDFILE="$DIR/agent.pid"
LOG="$DIR/agent.log"

case "${1:-}" in
  start)
    if [ ! -s "$TOKEN_FILE" ]; then
      openssl rand -hex 24 > "$TOKEN_FILE"
      chmod 600 "$TOKEN_FILE"
      echo "generated token: $TOKEN_FILE"
    fi
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (pid $(cat "$PIDFILE"))"; exit 0
    fi
    AGENT_TOKEN="$(cat "$TOKEN_FILE")" nohup python3 "$DIR/mac_agent.py" >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    curl -sf http://127.0.0.1:9999/api/health >/dev/null && echo "agent up on 127.0.0.1:9999" || { echo "agent failed to start — see $LOG"; exit 1; }
    ;;
  stop)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo "stopped" || echo "not running"
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running (pid $(cat "$PIDFILE"))"; curl -s http://127.0.0.1:9999/api/health; echo
    else
      echo "not running"
    fi
    ;;
  *) echo "usage: $0 start|stop|status"; exit 1;;
esac
