#!/usr/bin/env bash
# Watchdog for realistic-day test - reports status to telegram
# Used by cron job rday-watchdog
set -uo pipefail

REPORT_DIR="/home/hung/ai-hub/reports/realistic-day-2026-06-08"
SCRIPT="/home/hung/ai-hub/scripts/rday_status.sh"

# Check if runner is alive
if [[ ! -f "$REPORT_DIR/pid" ]]; then
    echo "❌ Realistic-Day test: no PID file (not running)"
    exit 0
fi

PID=$(cat "$REPORT_DIR/pid")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "❌ Realistic-Day test: DEAD (PID=$PID was the last known)"
    exit 0
fi

# Get status
STATUS=$($SCRIPT 2>&1)
echo "$STATUS"
echo
echo "───────────────"
echo "✅ Runner alive (PID=$PID)"
