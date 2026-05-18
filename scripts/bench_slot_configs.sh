#!/usr/bin/env bash
# Benchmark 1 cấu hình slot/ctx của Lite Q8 (port 8080) với 50 user × 5 turn.
# Usage: bench_slot_configs.sh <label> <ctx_size> <parallel>
# Vd:    bench_slot_configs.sh A_10x6k 61440 10
#        bench_slot_configs.sh B_12x4k 49152 12

set -euo pipefail

LABEL=${1:?missing label}
CTX_SIZE=${2:?missing ctx_size}
PARALLEL=${3:?missing parallel}
USERS=${USERS:-50}
TURNS=${TURNS:-5}
HUB=/home/hung/ai-hub
REPORT_DIR=$HUB/reports/bench_$(date +%Y%m%d_%H%M%S)_${LABEL}
mkdir -p "$REPORT_DIR"

echo "[bench] Label=$LABEL ctx=$CTX_SIZE parallel=$PARALLEL users=$USERS turns=$TURNS"
echo "[bench] Report dir: $REPORT_DIR"

cd "$HUB"

echo "[bench] Stopping any running services..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "llama-server" 2>/dev/null || true
sleep 2

# Khớp PARALLEL với GPU_CONCURRENCY trong app
export GPU_CONCURRENCY=$PARALLEL

echo "[bench] Starting Lite Q8 (port 8080) ctx=$CTX_SIZE parallel=$PARALLEL"
CTX_SIZE=$CTX_SIZE PARALLEL=$PARALLEL \
  LOG_FILE=$REPORT_DIR/llama-lite.log \
  PID_FILE=/tmp/aihub-llama-server.pid \
  ./scripts/start_lite_q8.sh

echo "[bench] Starting Background Q4 (port 8081)"
LOG_FILE=$REPORT_DIR/llama-bg.log \
  PID_FILE=/tmp/aihub-llama-background.pid \
  ./scripts/start_background_q4.sh

echo "[bench] Starting Reranker (port 8082)"
LOG_FILE=$REPORT_DIR/llama-rerank.log \
  PID_FILE=/tmp/aihub-reranker.pid \
  ./scripts/start_reranker.sh

echo "[bench] Starting AI Hub uvicorn"
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > "$REPORT_DIR/uvicorn.log" 2>&1 &
UVICORN_PID=$!

API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)
until curl -fsS -H "X-API-KEY: $API_KEY" http://127.0.0.1:8000/health >/dev/null 2>&1; do
  if ! kill -0 $UVICORN_PID 2>/dev/null; then
    echo "[bench] uvicorn died early. Tail of uvicorn.log:"
    tail -40 "$REPORT_DIR/uvicorn.log"
    exit 1
  fi
  sleep 0.5
done
echo "[bench] AI Hub ready"

echo "[bench] Starting GPU sampler (every 1s)"
nvidia-smi --query-gpu=timestamp,memory.used,memory.free,utilization.gpu \
  --format=csv,nounits -lms 1000 > "$REPORT_DIR/gpu.csv" 2>&1 &
GPU_PID=$!

echo "[bench] Running perf test users=$USERS turns=$TURNS"
START_EPOCH=$(date +%s)
set +e
AIHUB_PERF_USERS=$USERS \
AIHUB_PERF_TURNS=$TURNS \
AIHUB_PERF_TENANT=bench_$LABEL \
AIHUB_PERF_PROJECT=test \
AIHUB_PERF_OUTPUT="$REPORT_DIR" \
./venv/bin/python scripts/perf_test_20users.py \
  > "$REPORT_DIR/perf.stdout.log" 2>&1
PERF_RC=$?
set -e
END_EPOCH=$(date +%s)
DURATION=$((END_EPOCH-START_EPOCH))

echo "[bench] Perf test exit=$PERF_RC duration=${DURATION}s"

kill $GPU_PID 2>/dev/null || true

# Crash detection
LITE_DEAD=0
if ! curl -fsS http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
  LITE_DEAD=1
  echo "[bench] *** Lite Q8 (8080) DEAD after test ***"
fi
BG_DEAD=0
if ! curl -fsS http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then
  BG_DEAD=1
  echo "[bench] *** Background Q4 (8081) DEAD after test ***"
fi

# Snapshot summary
{
  echo "label=$LABEL"
  echo "ctx_size=$CTX_SIZE"
  echo "parallel=$PARALLEL"
  echo "users=$USERS"
  echo "turns=$TURNS"
  echo "duration_seconds=$DURATION"
  echo "perf_exit=$PERF_RC"
  echo "lite_dead_after=$LITE_DEAD"
  echo "bg_dead_after=$BG_DEAD"
  echo "peak_vram_used_mib=$(awk -F',' 'NR>1{gsub(/ /,"",$2); if($2+0>m)m=$2+0} END{print m}' "$REPORT_DIR/gpu.csv")"
  echo "min_vram_free_mib=$(awk -F',' 'NR>1{gsub(/ /,"",$3); v=$3+0; if(NR==2||v<m) m=v} END{print m}' "$REPORT_DIR/gpu.csv")"
} > "$REPORT_DIR/summary.txt"

echo "===== SUMMARY ====="
cat "$REPORT_DIR/summary.txt"
echo "==================="

echo "[bench] Stopping services..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "llama-server" 2>/dev/null || true
sleep 1

echo "[bench] Done. Artifacts in $REPORT_DIR"
