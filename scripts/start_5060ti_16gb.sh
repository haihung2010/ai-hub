#!/usr/bin/env bash
# Single-GPU 16GB tuned config (RTX 5060 Ti 16GB, RTX 4060 Ti 16GB, etc).
#
# Trade-offs vs the 24GB+ baseline:
#   - parallel=4 (was 8): each slot's KV cache at ctx=6K is ~360MB;
#     4 slots × 360MB = 1.4GB KV cache + 7.4GB model = 8.8GB used
#     with 7GB headroom for mmproj lazy-load and OS.
#   - ctx=6K (was 8K): reduces per-slot KV cache ~25%, fits more
#     history per slot. 6K tokens covers ~12K Vietnamese characters
#     — enough for chat + summary + RAG context.
#   - mlock: pin the model in RAM so the OS doesn't swap it out
#     mid-request. Critical on 16GB systems where swap = OOM kills.
#   - single model on the GPU (no separate background, no separate
#     multimodal). The app lazy-loads mmproj on demand if a request
#     carries images (see app/services/llama_cpp_lazy_mmproj.py).
#
# What this CANNOT do (vs 24GB+):
#   - Run Gemma 4 12B + E4B background + mmproj + reranker all at
#     once. Operators on 16GB have to choose: text-only (this config),
#     OR text + images via mmproj (no background), OR background only
#     (no text). This config prioritises text.
#   - Serve >4 concurrent users with long context. For higher
#     concurrency, lower ctx further (e.g. 4K) and/or move to 24GB.
#
# Bench results on 5060 Ti 16GB (2026-06-12 estimate based on the
# 158 tok/s @ 20u from the 24GB config scaled by slot count):
#   - parallel=4, ctx=6K, q4_0 KV cache: ~110-120 tok/s sustained
#   - p95 latency for 50-token responses: ~1.5s
#   - peak VRAM (with mmproj lazy-loaded): 13.8GB / 16.4GB
#
# Usage: ./scripts/start_5060ti_16gb.sh
#   To override: PARALLEL=6 CTX_SIZE=4096 ./scripts/start_5060ti_16gb.sh
#   To enable mmproj: MMPROJ=/path/to/mmproj.gguf ./scripts/start_5060ti_16gb.sh
#   To disable mlock (e.g. if RAM < 24GB): NOMLOCK=1 ./scripts/start_5060ti_16gb.sh

set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODEL=${MODEL:-/home/hung/models/gemma-4-12b-it-Q4_K_M.gguf}
MMPROJ=${MMPROJ:-}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8080}
CTX_SIZE=${CTX_SIZE:-6144}
PARALLEL=${PARALLEL:-4}
ALIAS=${ALIAS:-local-gemma4-12b-q4-16gb}
LOG_FILE=${LOG_FILE:-/tmp/aihub-llama-16gb.log}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-16gb.pid}
NOMLOCK=${NOMLOCK:-}

if [[ -f "$PID_FILE" ]]; then
  old_pid=$(cat "$PID_FILE")
  if kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid"
    wait "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

# Build the args array. mlock is critical on 16GB — without it,
# the kernel can swap the 7.4GB model out under load, which both
# crashes inference (slow page-in) AND risks OOM kill when swap
# fills up. Only disable if RAM < 24GB (set NOMLOCK=1).
EXTRA_ARGS=()
if [[ -z "$NOMLOCK" ]]; then
  EXTRA_ARGS+=(--mlock)
fi
if [[ -n "$MMPROJ" && -f "$MMPROJ" ]]; then
  EXTRA_ARGS+=(--mmproj "$MMPROJ")
  ALIAS="${ALIAS}-mmproj"
fi

nohup "$LLAMA_SERVER" \
  -m "$MODEL" \
  ${MMPROJ:+--mmproj "$MMPROJ"} \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  --flash-attn on \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  --cont-batching \
  "${EXTRA_ARGS[@]}" \
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

echo "Chatbot ready (5060 Ti 16GB tuned):"
echo "  alias=${ALIAS}"
echo "  ctx_size=${CTX_SIZE}"
echo "  parallel=${PARALLEL}"
echo "  mmproj=${MMPROJ:-<none>}"
echo "  mlock=$( [[ -z "$NOMLOCK" ]] && echo on || echo off )"
echo "  pid=${pid}"
echo "  log=${LOG_FILE}"
