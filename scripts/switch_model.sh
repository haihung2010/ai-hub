#!/usr/bin/env bash
# Switch llama.cpp Q8 server to a different model+config.
# Usage: ./switch_model.sh e4b_q4|e4b_q8|e2b_q4
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-}"
case "$MODE" in
  e2b_q4)
    MODEL_PATH=/home/hung/models/gemma-4-E2B-it-Q4_K_M.gguf
    CTX_SIZE=131072
    PARALLEL=16
    ALIAS=local-gemma4-e2b-q4
    ;;
  e4b_q4)
    MODEL_PATH=/home/hung/models/gemma-4-E4B-it-obliterated-Q4_K_M.gguf
    CTX_SIZE=98304
    PARALLEL=12
    ALIAS=local-gemma4-e4b-q4
    ;;
  e4b_q8)
    MODEL_PATH=/home/hung/models/gemma-4-E4B-it-obliterated-Q8_0.gguf
    CTX_SIZE=65536
    PARALLEL=8
    ALIAS=local-gemma4-e4b-q8
    ;;
  *)
    echo "Usage: $0 {e2b_q4|e4b_q4|e4b_q8}"
    exit 1
    ;;
esac

echo "[switch] $MODE → $MODEL_PATH (ctx=$CTX_SIZE par=$PARALLEL alias=$ALIAS)"

# Stop existing llama-server on 8080
PID_FILE=/tmp/aihub-llama-lite-q8.pid
if [[ -f "$PID_FILE" ]]; then
  old=$(cat "$PID_FILE")
  kill "$old" 2>/dev/null || true
  rm -f "$PID_FILE"
fi
pkill -f "llama-server .*--port 8080" 2>/dev/null || true
sleep 1

# Update LITE_MODEL alias in ai-hub .env so router selects correct model
sed -i "s|^LITE_MODEL=.*|LITE_MODEL=$ALIAS|" .env
sed -i "s|^SUMMARY_MODEL=.*|SUMMARY_MODEL=$ALIAS|" .env
sed -i "s|^STRUCTMEM_EXTRACTION_MODEL=.*|STRUCTMEM_EXTRACTION_MODEL=$ALIAS|" .env
sed -i "s|^STRUCTMEM_CONSOLIDATION_MODEL=.*|STRUCTMEM_CONSOLIDATION_MODEL=$ALIAS|" .env

# Start llama-server (no mmproj for E4B since file not present; vision off is fine for memory tests)
LLAMA_BIN=/home/hung/llama.cpp/build-cuda13/bin/llama-server
nohup "$LLAMA_BIN" \
  -m "$MODEL_PATH" \
  --host 127.0.0.1 --port 8080 \
  --ctx-size "$CTX_SIZE" \
  --parallel "$PARALLEL" \
  --n-gpu-layers 999 \
  --alias "$ALIAS" \
  --reasoning off \
  >/tmp/aihub-llama-lite-q8.log 2>&1 &
echo $! > "$PID_FILE"
echo "[switch] llama-server pid=$(cat $PID_FILE)"

# Wait until /v1/models responds
until curl -fsS "http://127.0.0.1:8080/v1/models" >/dev/null 2>&1; do sleep 0.5; done
echo "[switch] llama.cpp ready"

# Restart ai-hub
pkill -f "uvicorn app.main:app.*8000" 2>/dev/null || true
sleep 1
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/aihub-uvicorn.log 2>&1 &
disown

until curl -fsS -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2)" http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 0.5; done
echo "[switch] ai-hub ready, model alias=$ALIAS"
