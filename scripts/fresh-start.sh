#!/usr/bin/env bash
# fresh-start.sh — wipe all trading data for a clean $100K baseline.
#
# RUN ORDER (Chief PM decision 2026-07-04):
#   1. Back up the DB (this script refuses to run without a fresh backup).
#   2. Reset the Alpaca paper account to $100,000 in app.alpaca.markets
#      (web dashboard button — there is no API for it).
#   3. Run this script on the Oracle host: ./scripts/fresh-start.sh
#   4. It restarts backend + brain so in-memory state clears too.
#
# Wipes:  staged_orders, order_audit_log, positions, signal_outcomes,
#         trading_limits, eod_reports, alerts, threshold_store
# Keeps:  github_repo_scout (research), model_routing_log (cost telemetry),
#         version history, all code and config.
set -euo pipefail

DB="${DB_PATH:-/home/ubuntu/Market-AI/infra/db/marketflow.db}"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"

# Refuse to wipe unless a backup from the last 24h exists.
if ! find "$BACKUP_DIR" -name 'marketflow-pre-reset-*.db' -mmin -1440 2>/dev/null | grep -q .; then
  echo "ERROR: no pre-reset backup from the last 24h in $BACKUP_DIR."
  echo "Run: cp $DB $BACKUP_DIR/marketflow-pre-reset-\$(date +%Y%m%d-%H%M).db"
  exit 1
fi

echo "Backup found. Wiping trading tables in $DB …"
sudo python3 - "$DB" <<'EOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
tables = ["order_audit_log", "signal_outcomes", "positions", "staged_orders",
          "trading_limits", "eod_reports", "alerts", "threshold_store"]
for t in tables:
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        conn.execute(f"DELETE FROM {t}")
        print(f"  {t}: deleted {n} rows")
    except sqlite3.OperationalError as e:
        print(f"  {t}: skipped ({e})")
conn.commit()
conn.execute("VACUUM")
conn.close()
print("DB wipe complete.")
EOF

echo "Restarting backend + brain to clear in-memory state …"
cd "$(dirname "$DB")/../.."
sudo docker-compose restart backend brain

echo "Fresh start complete. Verify: dashboard should show \$100,000 cash,"
echo "0 positions, 0 signals. Trading resumes with CASH_ONLY_MODE=true."
