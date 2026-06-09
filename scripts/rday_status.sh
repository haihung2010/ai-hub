#!/usr/bin/env bash
# /rday shortcut — quick status of the realistic-day test
# Usage: ./scripts/rday_status.sh
set -euo pipefail

REPORT_DIR="/home/hung/ai-hub/reports/realistic-day-2026-06-08"
if [[ ! -d "$REPORT_DIR" ]]; then
    echo "❌ No report dir at $REPORT_DIR"
    exit 1
fi

echo "═══════════════════════════════════════"
echo "🧪 Realistic-Day Test Status"
echo "═══════════════════════════════════════"
echo "Time (ICT): $(TZ=Asia/Ho_Chi_Minh date +'%Y-%m-%d %H:%M:%S')"

if [[ -f "$REPORT_DIR/pid" ]]; then
    PID=$(cat "$REPORT_DIR/pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Runner: ✅ alive (PID=$PID)"
    else
        echo "Runner: ❌ dead (PID=$PID, last seen: $(ps -o lstart= -p $PID 2>/dev/null || echo unknown))"
    fi
else
    echo "Runner: ⚠️  no PID file"
fi

CYCLE=$(jq -r '.cycle' "$REPORT_DIR/state_cycle.json" 2>/dev/null || echo "?")
echo "Current cycle: $CYCLE"

if [[ -f "$REPORT_DIR/cycle_summaries.jsonl" ]]; then
    echo ""
    echo "─── Per-cycle summary ───"
    echo "cyc | total | ok | err% | p50  | p95  | max  | users | phases(A/M/L)"
    tail -10 "$REPORT_DIR/cycle_summaries.jsonl" | jq -r '
        [.cycle, .total, .ok, .err_rate, .p50_ms, .p95_ms, .max_ms, .users, "\(.phases.active // 0)/\(.phases.memory_verify // 0)/\(.phases.learning_probe // 0)"]
        | @tsv' | column -t -s $'\t'
fi

if [[ -f "$REPORT_DIR/adaptive_scaler.log" ]]; then
    echo ""
    echo "─── Last 5 scaler decisions ───"
    tail -5 "$REPORT_DIR/adaptive_scaler.log"
fi

if [[ -f "$REPORT_DIR/memory_recall.jsonl" ]]; then
    echo ""
    echo "─── Memory recall (avg per cycle) ───"
    jq -s 'group_by(.cycle) | map({cycle: .[0].cycle, n: length, avg_score: (map(.keyword_score) | add / length)})' "$REPORT_DIR/memory_recall.jsonl" 2>/dev/null | jq -r '.[] | "Cycle \(.cycle): avg=\(.avg_score|tostring|.[0:5]) n=\(.n)"'
fi

if [[ -f "$REPORT_DIR/learning_curve.jsonl" ]]; then
    echo ""
    echo "─── Learning curve (avg latency delta per cycle) ───"
    jq -s 'group_by(.cycle) | map({cycle: .[0].cycle, n: length, avg_delta_pct: (map(.delta_pct) | add / length)})' "$REPORT_DIR/learning_curve.jsonl" 2>/dev/null | jq -r '.[] | "Cycle \(.cycle): Δ=\(.avg_delta_pct|tostring|.[0:6])% n=\(.n)"'
fi

if [[ -f "$REPORT_DIR/ihi_pulses.jsonl" ]]; then
    echo ""
    echo "─── IHI pulses (30-min cycles) ───"
    tail -5 "$REPORT_DIR/ihi_pulses.jsonl" | jq -r '"c\(.cycle) m\(.minute) scrape=\(.scrape_id) status=\(.status) rows=\(.rows_added)"'
fi

echo ""
echo "═══════════════════════════════════════"
echo "🛑 Stop: touch $REPORT_DIR/stop_signal.txt"
