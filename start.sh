#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/5] Starting Chatbot (12B Q4, 12 slots, q4_0 cache) on port 8080..."
./scripts/start_lite_q8.sh   # start_lite_q8.sh is now updated to launch 12B Q4 (see Task 15)

echo "[2/5] Starting Background Q4 on port 8081..."
./scripts/start_background_q4.sh

echo "[3/5] Starting iHi Sensor (E2B Q4, 40 slots) on port 8083..."
./scripts/start_ihi_sensor.sh

echo "[4/5] Starting Reranker on port 8082..."
./scripts/start_reranker.sh

echo "[5/5] Starting AI Hub on port 8000..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 0.5
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/aihub-uvicorn.log 2>&1 &

API_KEY=$(grep "^API_KEY=" .env | cut -d= -f2 | tr -d '"')
until curl -fsS -H "X-API-KEY: $API_KEY" http://127.0.0.1:8000/health | grep -q '"status":"ok"'; do
  sleep 0.5
done

echo ""
echo "=== AI Hub 2-Mode Ready ==="
echo "  Chatbot  (port 8080): 12B Q4, 12 slots, ctx=8K, q4_0 cache → multi-user normal chat"
echo "  iHi      (port 8083): E2B Q4, 40 slots, ctx=8K   → sensor check every 30s"
echo "  Reranker (port 8082): bge-reranker-v2-m3"
echo "  API      (port 8000): http://localhost:8000"
echo ""