#!/usr/bin/env bash
# Scope C: SINGLE 12B Q4 on port 8080.
# Background memory tasks route to same instance (configured in app/core/config.py).
# Simpler ops, but memory tasks steal parallel slots from primary users.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-12}
ALIAS=${ALIAS:-local-gemma4-12b-q4-scope-c-single}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-scope-c.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-scope-c.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instance
if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"; wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX_SIZE" --parallel "$PARALLEL" \
  --n-gpu-layers 999 --alias "$ALIAS" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
  if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "Scope C ready: pid=$pid, port=$PORT, log=$LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Scope C did not become ready in 30s"
tail -20 "$LOG_FILE"
exit 1
