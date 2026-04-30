#!/usr/bin/env bash
set -euo pipefail

PORT=${PORT:-8080}
PID_FILE=${PID_FILE:-/tmp/aihub-llama-server.pid}

if [[ -f "$PID_FILE" ]]; then
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    wait "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

pkill -f "llama-server .*--port ${PORT}" 2>/dev/null || true

echo "Local llama-server stopped on port ${PORT}"
