#!/usr/bin/env bash
# Start the Langfuse v3 local stack and wait for the web UI to come up.
# Usage:
#   ./scripts/start_langfuse.sh
#
# Brings up docker/langfuse/docker-compose.yml, polls the public health
# endpoint on http://localhost:3000/api/public/health for up to 60s, and
# prints instructions for the operator to create the first account.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_DIR="${REPO_ROOT}/docker/langfuse"
HEALTH_URL="http://localhost:3000/api/public/health"
MAX_WAIT_SECONDS=60

cd "${COMPOSE_DIR}"

echo "[start_langfuse] Bringing up Langfuse v3 stack in ${COMPOSE_DIR} ..."
docker compose up -d

echo "[start_langfuse] Waiting for ${HEALTH_URL} (max ${MAX_WAIT_SECONDS}s) ..."

elapsed=0
interval=2
healthy=0
last_status=""

while [ "${elapsed}" -lt "${MAX_WAIT_SECONDS}" ]; do
    if last_status="$(curl -fsS -o /dev/null -w "%{http_code}" "${HEALTH_URL}" 2>/dev/null)"; then
        if [ "${last_status}" = "200" ]; then
            healthy=1
            break
        fi
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
done

if [ "${healthy}" -ne 1 ]; then
    echo "[start_langfuse] Langfuse did not become healthy within ${MAX_WAIT_SECONDS}s." >&2
    echo "[start_langfuse] Last HTTP status: ${last_status:-<no response>}" >&2
    echo "[start_langfuse] Tail of langfuse-web logs:" >&2
    docker compose logs --tail=50 langfuse-web >&2 || true
    exit 1
fi

cat <<EOF
[start_langfuse] Langfuse ready.
  - UI:            http://localhost:3000
  - Public health: ${HEALTH_URL}
  - OTLP traces:   http://localhost:3000/api/public/otel/v1/traces
  - Container logs: docker compose -f ${COMPOSE_DIR}/docker-compose.yml logs -f

Next steps:
  1. Open http://localhost:3000 in your browser.
  2. Create the first account (this is the Langfuse org owner).
  3. Create a project (e.g., "ai-hub") and copy the public + secret keys
     into the existing LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY vars
     in the repository's .env (do not commit them).
  4. To stop the stack:  docker compose -f ${COMPOSE_DIR}/docker-compose.yml down
  5. To wipe state:      docker compose -f ${COMPOSE_DIR}/docker-compose.yml down -v
EOF
