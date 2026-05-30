#!/usr/bin/env bash
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8083}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-40}
ALIAS=${ALIAS:-local-gemma4-e2b-q4-ihi}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-ihi.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-ihi.pid}

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
  --host "$HOST" \
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

echo "Waiting for ihi server on port $PORT..."
until curl -fsS "http://${HOST}:${PORT}/v1/models" >/dev/null 2>&1; do
  if ! kill -0 "$pid" 2>/dev/null; then
    cat "$LOG_FILE"
    exit 1
  fi
  sleep 0.5
done

echo "iHi server ready: alias=$ALIAS, ctx_size=$CTX_SIZE, parallel=$PARALLEL, pid=$pid, log=$LOG_FILE"