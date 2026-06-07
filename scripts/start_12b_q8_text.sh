#!/usr/bin/env bash
# Launch 12B Q8_0 as TEXT-ONLY chatbot on port 8080.
# Used for Q8-textonly config (Q8 standalone, no multimodal — gemma4uv
# mmproj not yet supported by llama.cpp 8981). Text-only comparison
# against Q4-combo / Q6-combo (which pair 12B text + E2B multimodal).
# Usage: ./start_12b_q8_text.sh
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q8_0.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-8}
ALIAS=${ALIAS:-local-gemma4-12b-q8-text}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q8-text.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q8-text.pid}

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
        echo "12B Q8 text-only ready: pid=$pid, port=$PORT, log=$LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "ERROR: 12B Q8 did not become ready in 30s"
cat "$LOG_FILE" | tail -20
exit 1
