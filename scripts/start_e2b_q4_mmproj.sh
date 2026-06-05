#!/usr/bin/env bash
# Launch E2B Q4 + mmproj as multimodal IHI sensor LLM on port 8083.
# For Q4-combo and Q6-combo configs (paired with 12B text on 8080).
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
MMPROJ=${MMPROJ:-/home/hung/models/mmproj-gemma-4-E2B-it-F16.gguf}
PORT=${PORT:-8083}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-40}
ALIAS=${ALIAS:-local-gemma4-e2b-q4-mmproj-ihi}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-e2b-mmproj.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-e2b-mmproj.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }
[[ -f "$MMPROJ" ]] || { echo "ERROR: mmproj not found at $MMPROJ"; exit 2; }

if [[ -f "$PID_FILE" ]]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid"
        wait "$old_pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "E2B Q4 + mmproj ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: E2B did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
