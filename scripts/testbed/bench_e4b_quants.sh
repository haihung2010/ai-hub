#!/usr/bin/env bash
# Benchmark 5 quants of E4B obliterated with fast profile (16 turn each).
# Each quant runs E2B Q4 chat alias (so app config doesn't change), with --mmproj for vision support.
#
# Usage: ./scripts/testbed/bench_e4b_quants.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)
MMPROJ=/home/hung/models/mmproj-google_gemma-4-E4B-it-f16.gguf
LLAMA=/home/hung/llama.cpp/build-cuda13/bin/llama-server
PARALLEL=8
CTX_SIZE=65536
LOG=/tmp/aihub-llama-e4b-bench.log

QUANTS=(Q4_K_M Q5_0 Q5_K_M Q6_K Q8_0)

stop_llama() {
  local pid_file=/tmp/aihub-llama-server.pid
  if [[ -f "$pid_file" ]]; then
    local pid
    pid=$(cat "$pid_file")
    kill "$pid" 2>/dev/null || true
    sleep 3
  fi
  pkill -f "llama-server .*--port 8080" 2>/dev/null || true
  sleep 2
}

start_llama() {
  local model_path=$1
  local alias=$2
  rm -f /tmp/aihub-llama-server.pid
  nohup "$LLAMA" \
    -m "$model_path" \
    --mmproj "$MMPROJ" \
    --host 127.0.0.1 --port 8080 \
    --ctx-size "$CTX_SIZE" --parallel "$PARALLEL" \
    --n-gpu-layers 999 \
    --alias "$alias" \
    --reasoning off \
    >"$LOG" 2>&1 &
  echo $! > /tmp/aihub-llama-server.pid

  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:8080/v1/models" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "[bench] FAIL: $alias didn't come up in 60s"
  tail -20 "$LOG"
  return 1
}

restart_uvicorn() {
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 2
  nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/aihub-uvicorn.log 2>&1 &
  for i in $(seq 1 30); do
    if curl -fsS -H "X-API-KEY: $API_KEY" http://127.0.0.1:8000/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "[bench] FAIL: uvicorn didn't come up"
  return 1
}

# Use the same alias the app expects (LITE_MODEL=local-gemma4-e2b-q4) so we don't
# have to touch .env between quants. The alias is just a label.
ALIAS=local-gemma4-e2b-q4

for q in "${QUANTS[@]}"; do
  MODEL=/home/hung/models/gemma-4-E4B-it-obliterated-$q.gguf
  REPORT=reports/testbed/aihub-e4b-$q.json
  echo
  echo "============================================================"
  echo "[bench] Quant: $q  ($(du -h "$MODEL" | cut -f1))"
  echo "============================================================"

  stop_llama
  if ! start_llama "$MODEL" "$ALIAS"; then
    echo "[bench] skip $q"
    continue
  fi
  nvidia-smi --query-gpu=memory.used --format=csv,noheader

  restart_uvicorn

  ./venv/bin/python scripts/testbed/run_chats.py --profile fast --report "$REPORT"
done

stop_llama
echo
echo "[bench] All quants tested. Reports in reports/testbed/aihub-e4b-*.json"
