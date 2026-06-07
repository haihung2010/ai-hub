#!/usr/bin/env bash
# Scope B: 12B Q4 on BOTH ports — 8080 (primary chat) + 8081 (background memory).
# For Phase 2 testing of "12B everywhere" hypothesis.
# Each instance uses lower parallel (10 + 4) to fit in 16GB together.
set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}

# Launch 8080 (primary chat, parallel=10)
CTX_SIZE_8080=${CTX_SIZE_8080:-8192}
PARALLEL_8080=${PARALLEL_8080:-10}
ALIAS_8080=${ALIAS_8080:-local-gemma4-12b-q4-scope-b-chat}
LOG_8080=${LOG_8080:-/tmp/aihub-llama-12b-scope-b-8080.log}
PID_8080=${PID_8080:-/tmp/aihub-llama-12b-scope-b-8080.pid}

# Launch 8081 (background memory, parallel=4)
CTX_SIZE_8081=${CTX_SIZE_8081:-8192}
PARALLEL_8081=${PARALLEL_8081:-4}
ALIAS_8081=${ALIAS_8081:-local-gemma4-12b-q4-scope-b-bg}
LOG_8081=${LOG_8081:-/tmp/aihub-llama-12b-scope-b-8081.log}
PID_8081=${PID_8081:-/tmp/aihub-llama-12b-scope-b-8081.pid}

[[ -f "$MODEL" ]] || { echo "ERROR: $MODEL not found"; exit 2; }

# Kill any existing instances
pkill -f "llama-server .*--port 8080" 2>/dev/null || true
pkill -f "llama-server .*--port 8081" 2>/dev/null || true
sleep 1

# Start 8080
nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size "$CTX_SIZE_8080" --parallel "$PARALLEL_8080" \
  --n-gpu-layers 999 --alias "$ALIAS_8080" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_8080" 2>&1 &
echo $! > "$PID_8080"

# Start 8081
nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port 8081 \
  --ctx-size "$CTX_SIZE_8081" --parallel "$PARALLEL_8081" \
  --n-gpu-layers 999 --alias "$ALIAS_8081" \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 --cont-batching \
  >"$LOG_8081" 2>&1 &
echo $! > "$PID_8081"

# Wait both ready (max 60s — both load in parallel)
for i in {1..60}; do
  ok_8080=$(curl -fsS -m 1 http://127.0.0.1:8080/v1/models >/dev/null 2>&1 && echo y || echo n)
  ok_8081=$(curl -fsS -m 1 http://127.0.0.1:8081/v1/models >/dev/null 2>&1 && echo y || echo n)
  if [[ "$ok_8080" == "y" && "$ok_8081" == "y" ]]; then
    echo "Scope B ready: 8080=$ALIAS_8080, 8081=$ALIAS_8081"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Scope B did not become ready in 60s"
echo "--- 8080 log ---"; tail -10 "$LOG_8080"
echo "--- 8081 log ---"; tail -10 "$LOG_8081"
exit 1
