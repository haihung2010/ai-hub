#!/usr/bin/env bash
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/bge-reranker-v2-m3-q8_0.gguf}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8082}
ALIAS=${ALIAS:-bge-reranker-v2-m3}
LOG_FILE=${LOG_FILE:-/tmp/aihub-reranker.log}
PID_FILE=${PID_FILE:-/tmp/aihub-reranker.pid}

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
  --reranking \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size 4096 \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
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

echo "Reranker ready: alias=${ALIAS}, port=${PORT}, pid=${pid}"
