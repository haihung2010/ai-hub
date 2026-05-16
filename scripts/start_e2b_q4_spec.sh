#!/usr/bin/env bash
# E4B Q4 target + E2B Q4 draft (speculative decoding)
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-E4B-it-obliterated-Q4_K_M.gguf}
DRAFT_MODEL=${DRAFT_MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-131072}
PARALLEL=${PARALLEL:-16}
DRAFT_N_MAX=${DRAFT_N_MAX:-8}
DRAFT_P_MIN=${DRAFT_P_MIN:-0.6}
ALIAS=${ALIAS:-local-gemma4-e4b-q4-spec}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-spec.log}
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
sleep 1

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --model-draft "$DRAFT_MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --n-gpu-layers-draft 999 \
  --spec-draft-n-max "$DRAFT_N_MAX" \
  --spec-draft-p-min "$DRAFT_P_MIN" \
  --alias "$ALIAS" \
  --reasoning off \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

until curl -fsS "http://${HOST}:${PORT}/v1/models" >/dev/null; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "E2B Q4 + draft failed to start:"
    tail -40 "$LOG_FILE"
    exit 1
  fi
  sleep 0.5
done

echo "E2B Q4 + draft ready: alias=${ALIAS} parallel=${PARALLEL} draft_n_max=${DRAFT_N_MAX} pid=${pid} log=${LOG_FILE}"
