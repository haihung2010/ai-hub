#!/usr/bin/env bash
# Test-bed: reset → seed → run multi-tenant chat scenarios
#
# Usage:
#   scripts/testbed/run.sh                 # fast smoke (1 user/tenant)
#   scripts/testbed/run.sh full            # full load (5 user/tenant)
#   scripts/testbed/run.sh full report.json
set -euo pipefail

PROFILE="${1:-fast}"
REPORT="${2:-}"
SCALE="${3:-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ "$PROFILE" != "fast" && "$PROFILE" != "full" ]]; then
  echo "usage: $0 [fast|full] [report.json] [scale]" >&2
  exit 2
fi

echo "[run.sh] profile=$PROFILE scale=$SCALE"

API="http://localhost:8000"
API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)

echo "[run.sh] check AI Hub at $API"
if ! curl -fsS -H "X-API-KEY: $API_KEY" "$API/health" >/dev/null 2>&1; then
  echo "[run.sh] AI Hub không phản hồi tại $API. Chạy ./start.sh trước." >&2
  exit 3
fi

echo "[run.sh] step 1/3 — reset ephemeral data"
./venv/bin/python scripts/testbed/reset.py

echo "[run.sh] step 2/3 — seed knowledge cards"
./venv/bin/python scripts/testbed/seed_knowledge.py --api "$API"

echo "[run.sh] step 3/3 — run chats"
RUN_ARGS=(--profile "$PROFILE" --api "$API" --scale "$SCALE")
if [[ -n "$REPORT" ]]; then
  RUN_ARGS+=(--report "$REPORT")
fi
./venv/bin/python scripts/testbed/run_chats.py "${RUN_ARGS[@]}"
