#!/usr/bin/env bash
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-E4B-it-obliterated-Q8_0.gguf}
MMPROJ=${MMPROJ:-/home/hung/models/mmproj-gemma-4-E2B-it-F16.gguf}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-65536}
PARALLEL=${PARALLEL:-8}
ALIAS=${ALIAS:-local-gemma4-e4b-q8}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-lite-q8.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-server.pid}

if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"
    wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

MMPROJ_ARGS=()
if [[ "${ENABLE_MMPROJ:-0}" == "1" && -f "$MMPROJ" ]]; then
  MMPROJ_ARGS=(--mmproj "$MMPROJ")
  echo "Vision enabled: $MMPROJ"
else
  echo "Vision disabled (set ENABLE_MMPROJ=1 to enable)"
fi

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  "${MMPROJ_ARGS[@]}" \
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

until curl -fsS "http://${HOST}:${PORT}/v1/models" >/dev/null; do
  if ! kill -0 "$pid" 2>/dev/null; then
    cat "$LOG_FILE"
    exit 1
  fi
  sleep 0.5
done

echo "Lite Q8 ready: alias=${ALIAS}, ctx_size=${CTX_SIZE}, parallel=${PARALLEL}, pid=${pid}, log=${LOG_FILE}"
