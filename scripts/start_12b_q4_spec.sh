#!/usr/bin/env bash
# Launch 12B Q4 with SPECULATIVE DECODING using E2B Q4 as draft model.
# Theory: 2-3x speedup for single-user if accept rate is 50-70%.
# Multi-user: speedup may regress (draft work competes with parallel slots).
# For Phase 3 evaluation.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
TARGET_MODEL=${TARGET_MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
DRAFT_MODEL=${DRAFT_MODEL:-/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-8192}
PARALLEL=${PARALLEL:-12}
DRAFT_MAX=${DRAFT_MAX:-8}
ALIAS=${ALIAS:-local-gemma4-12b-q4-spec}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-12b-q4-spec.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-12b-q4-spec.pid}

[[ -f "$TARGET_MODEL" ]] || { echo "ERROR: $TARGET_MODEL not found"; exit 2; }
[[ -f "$DRAFT_MODEL" ]] || { echo "ERROR: $DRAFT_MODEL not found"; exit 2; }

# Kill existing
if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"; wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

nohup "$LLAMA_SERVER" \
  -m "$TARGET_MODEL" \
  --model-draft "$DRAFT_MODEL" \
  --spec-draft-n-max "$DRAFT_MAX" \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX_SIZE" --parallel "$PARALLEL" \
  --n-gpu-layers 999 --alias "$ALIAS" \
  --reasoning off --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 --cont-batching \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

for i in {1..30}; do
  if curl -fsS -m 1 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "12B Q4 + E2B draft ready: pid=$pid, port=$PORT, log=$LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: 12B Q4 + E2B draft did not become ready in 30s"
tail -20 "$LOG_FILE"
exit 1
