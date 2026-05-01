#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/2] Starting llama.cpp Q8 on port 8080..."
./scripts/start_lite_q8.sh

echo "[2/2] Starting AI Hub on port 8000..."
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 0.5
nohup ./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/aihub-uvicorn.log 2>&1 &

until curl -fsS -H "X-API-KEY: $(grep '^API_KEY=' .env | cut -d= -f2)" http://127.0.0.1:8000/health >/dev/null; do
  sleep 0.5
done

echo "AI Hub ready: http://localhost:8000"
echo "Logs: /tmp/aihub-uvicorn.log"
