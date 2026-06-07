#!/usr/bin/env bash
# Launch 12B Q4_K_M as TEXT-ONLY chatbot on port 8080 — P3 variant.
# P3: CTX_SIZE=12288 (vs P0=8192) — bigger ctx for long context.
# For use in Q4-combo config (12B text + E2B multimodal).
# Usage: ./start_12b_q4_p3.sh
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-12288}
PARALLEL=${PARALLEL:-12}
ALIAS=${ALIAS:-local-gemma4-12b-q4-text-p3}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q4-p3.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q4-p3.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instance on this port
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

# Wait for ready (max 30s)
for i in {1..30}; do
    if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "12B Q4 text-only ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: 12B Q4 did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
