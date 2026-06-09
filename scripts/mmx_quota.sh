#!/usr/bin/env bash
# /mmx shortcut — manual MiniMax account usage check (user-triggered, no auto-monitor)
# Usage: ./scripts/mmx_quota.sh
# Source: /home/hung/ai-hub/.env (MINIMAX_API_KEY, MINIMAX_BASE_URL)
set -euo pipefail

ENV_FILE="/home/hung/ai-hub/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ No $ENV_FILE"
    exit 1
fi

PFX="MINIMAX"
API_KEY=$(grep "^${PFX}_API_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"\n')
BASE_URL=$(grep "^${PFX}_BASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"\n' || echo "https://api.minimax.io/v1")
ENABLED=$(grep "^${PFX}_ENABLED=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"\n' || echo "false")
MODEL=$(grep "^${PFX}_MODEL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"\n' || echo "?")

if [[ "$ENABLED" != "true" ]]; then
    echo "⚠️  MiniMax is DISABLED in .env (ENABLED=$ENABLED)"
    echo "Set MINIMAX_ENABLED=true to use."
    exit 0
fi

if [[ -z "$API_KEY" ]]; then
    echo "❌ MINIMAX_API_KEY missing in $ENV_FILE"
    exit 1
fi

echo "═══════════════════════════════════════"
echo "💳 MiniMax Quota Check (manual)"
echo "═══════════════════════════════════════"
echo "Base URL: $BASE_URL"
echo "Model:    $MODEL"
echo "Time:     $(TZ=Asia/Ho_Chi_Minh date +'%Y-%m-%d %H:%M:%S %Z')"
echo ""

# Try a few candidate endpoints; the MiniMax docs are unclear
# Most provider account/quota endpoints look like:
#   /v1/account/usage, /v1/billing/credit, /v1/dashboard/billing/credit_grants
declare -a ENDPOINTS=(
    "/v1/account/usage"
    "/v1/billing/credit"
    "/v1/dashboard/billing/credit_grants"
    "/v1/usage"
    "/v1/account"
)

ok=0
for ep in "${ENDPOINTS[@]}"; do
    url="$BASE_URL$ep"
    echo "── Trying $ep ──"
    resp=$(curl -s -m 8 -w "\n__HTTP_CODE__:%{http_code}\n" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        "$url" 2>&1)
    code=$(echo "$resp" | grep '__HTTP_CODE__' | cut -d: -f2)
    body=$(echo "$resp" | grep -v '__HTTP_CODE__')
    if [[ "$code" == "200" ]]; then
        echo "✅ $ep returned 200"
        echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
        ok=1
        break
    else
        echo "   HTTP $code — skipping"
    fi
done

if [[ "$ok" == "0" ]]; then
    echo ""
    echo "❌ No MiniMax account endpoint returned 200."
    echo "Try these manually in browser (login required):"
    echo "  • https://api.minimax.io/user-center/billing/credit"
    echo "  • https://platform.minimax.io/user-center/billing/credit"
    echo "  • https://api.minimax.io/dashboard"
    echo ""
    echo "If you find the right endpoint, please update ENDPOINTS[] in this script."
fi
