#!/usr/bin/env bash
# AI Hub H: E4B Q4 @ 12 + E2B Q4 BG @ 4 + vision + reranker
# GPU: RTX 5060 Ti 16GB

set -euo pipefail

LLAMA_SERVER=${LLAMA_SERVER:-/home/hung/llama.cpp/build-cuda13/bin/llama-server}
MODELS_DIR=${MODELS_DIR:-/home/hung/models}

# Kill existing llama servers
pkill -f llama-server 2>/dev/null || true
sleep 2

# Port 8080: E4B Q4 @ 12 slots - primary chat
nohup "$LLAMA_SERVER" \
  -m "$MODELS_DIR/gemma-4-E4B-it-obliterated-Q4_K_M.gguf" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 16384 --parallel 12 \
  --n-gpu-layers 999 \
  --alias local-gemma4-e4b-q4 \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --cont-batching \
  >/tmp/llama-8080.log 2>&1 &
echo "8080 E4B Q4 PID=$!"

# Wait for 8080
until curl -fs http://127.0.0.1:8080/v1/models >/dev/null 2>&1; do sleep 0.5; done
echo "8080 ready"

# Port 8081: E2B Q4 BG @ 4 slots + mmproj vision
nohup "$LLAMA_SERVER" \
  -m "$MODELS_DIR/gemma-4-E2B-it-Q4_K_M.gguf" \
  --mmproj "$MODELS_DIR/mmproj-gemma-4-E2B-it-F16.gguf" \
  --host 127.0.0.1 --port 8081 \
  --ctx-size 16384 --parallel 4 \
  --n-gpu-layers 999 \
  --alias local-gemma4-e2b-q4-bg \
  --reasoning off --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --cont-batching \
  >/tmp/llama-8081.log 2>&1 &
echo "8081 E2B BG PID=$!"

# Wait for 8081
until curl -fs http://127.0.0.1:8081/v1/models >/dev/null 2>&1; do sleep 0.5; done
echo "8081 ready"

# Port 8082: Reranker Q8
nohup "$LLAMA_SERVER" \
  -m "$MODELS_DIR/bge-reranker-v2-m3-q8_0.gguf" \
  --host 127.0.0.1 --port 8082 \
  --ctx-size 4096 --parallel 2 \
  --n-gpu-layers 999 \
  --alias local-reranker-q8 \
  --reasoning off --cont-batching \
  >/tmp/llama-8082.log 2>&1 &
echo "8082 Reranker PID=$!"

# Wait for 8082
until curl -fs http://127.0.0.1:8082/v1/models >/dev/null 2>&1; do sleep 0.5; done
echo "8082 ready"

echo ""
echo "=== H Config Ready ==="
echo "8080: E4B Q4 @ 12 slots (primary chat)"
echo "8081: E2B Q4 @ 4 slots + mmproj (BG + vision)"
echo "8082: Reranker Q8 @ 2 slots (RAG)"
