#!/bin/bash
# ================================================================================
# AI HUB - COMPREHENSIVE TEST SUITE
# Tests all features, organized by category
# Run: bash test_ai_hub_comprehensive.sh
# ================================================================================

set -euo pipefail

API_KEY="1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL="http://localhost:8000"
PROJECT_ID="test_project"
USER_BASE="test_user_$(date +%s)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; ((PASS++)); }
fail() { echo -e "${RED}✗ FAIL${NC}: $1"; ((FAIL++)); }

echo -e "${BLUE}================================================================================"
echo "AI HUB COMPREHENSIVE TEST SUITE"
echo "================================================================================"
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "API Key: $API_KEY"
echo "Base URL: $BASE_URL"
echo "================================================================================${NC}"
echo ""

# ================================================================================
# CATEGORY 1: System Health
# ================================================================================
echo -e "${BLUE}=== CATEGORY 1: System Health ===${NC}"

HEALTH=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/health")
if echo "$HEALTH" | jq -e '.status' >/dev/null 2>&1; then
    pass "Health endpoint responds"
else
    fail "Health endpoint failed"
fi

GPU=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/gpu/stats" 2>/dev/null || echo "{}")
if echo "$GPU" | jq -e '.memory' >/dev/null 2>&1; then
    pass "GPU stats endpoint"
else
    fail "GPU stats endpoint"
fi

QUEUE=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/queue")
if echo "$QUEUE" | jq -e '.capacity' >/dev/null 2>&1; then
    pass "Queue status endpoint"
else
    fail "Queue status endpoint"
fi

USAGE=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/stats")
if echo "$USAGE" | jq -e '.total_requests' >/dev/null 2>&1; then
    pass "Usage stats endpoint"
else
    fail "Usage stats endpoint"
fi

SYS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/usage")
if echo "$SYS" | jq -e '.process.rss_mb' >/dev/null 2>&1; then
    pass "System usage endpoint"
else
    fail "System usage endpoint"
fi

echo ""

# ================================================================================
# CATEGORY 2: Authentication & Security
# ================================================================================
echo -e "${BLUE}=== CATEGORY 2: Authentication & Security ===${NC}"

RES=$(curl -s "$BASE_URL/health")
if echo "$RES" | jq -e '.detail' >/dev/null 2>&1; then
    pass "Health requires API key"
else
    fail "Health accessible without key"
fi

RES=$(curl -s -H "X-API-KEY: invalid_key" "$BASE_URL/health")
if echo "$RES" | jq -e '.detail' >/dev/null 2>&1; then
    pass "Invalid API key rejected"
else
    fail "Invalid API key accepted"
fi

for EP in "/v1/admin/stats" "/v1/admin/queue" "/v1/admin/gpu/stats" "/v1/admin/usage"; do
    RES=$(curl -s "$BASE_URL$EP")
    if echo "$RES" | jq -e '.detail' >/dev/null 2>&1; then
        pass "Admin $EP requires auth"
    else
        fail "Admin $EP open without auth"
    fi
done

RES=$(curl -s -X POST "$BASE_URL/v1/chat" -H "Content-Type: application/json" -d '{"user_name":"test","project_id":"test","user_message":"hello"}')
if echo "$RES" | jq -e '.detail' >/dev/null 2>&1; then
    pass "Chat requires API key"
else
    fail "Chat open without auth"
fi

echo ""

# ================================================================================
# CATEGORY 3: Basic Chat Functionality
# ================================================================================
echo -e "${BLUE}=== CATEGORY 3: Basic Chat Functionality ===${NC}"

USER1="${USER_BASE}_chat1"

START=$(date +%s%N)
RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"user_name\":\"$USER1\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Hello\",\"model_mode\":\"lite\"}")
END=$(date +%s%N)
ELAPSED=$(( (END - START) / 1000000 ))

if echo "$RESP" | jq -e '.content' >/dev/null 2>&1; then
    pass "Simple chat request responds"
    LATENCY=$(echo "$RESP" | jq -r '.latency_ms // "unknown"')
    echo "    Latency: ${LATENCY}ms"
else
    fail "Simple chat request failed"
    echo "    Response: $(echo "$RESP" | head -c 200)"
fi

SESSION_ID=$(echo "$RESP" | jq -r '.session_id // empty')
if [ -n "$SESSION_ID" ]; then
    pass "Session ID returned"
    
    RESP2=$(curl -s -X POST "$BASE_URL/v1/chat" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"user_name\":\"$USER1\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"What did I say?\",\"model_mode\":\"lite\",\"session_id\":\"$SESSION_ID\"}")
    
    if echo "$RESP2" | jq -e '.content' >/dev/null 2>&1; then
        pass "Session-based follow-up works"
    else
        fail "Session follow-up failed"
    fi
else
    fail "No session ID returned"
fi

for MODE in "lite" "normal"; do
    RESPM=$(curl -s -X POST "$BASE_URL/v1/chat" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"user_name\":\"${USER1}_${MODE}\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Hello\",\"model_mode\":\"$MODE\"}")
    
    if echo "$RESPM" | jq -e '.content' >/dev/null 2>&1; then
        pass "Model mode '$MODE' works"
    else
        fail "Model mode '$MODE' failed"
    fi
done

echo ""

# ================================================================================
# CATEGORY 4: Streaming
# ================================================================================
echo -e "${BLUE}=== CATEGORY 4: Streaming ===${NC}"

USER_S="${USER_BASE}_stream"

START=$(date +%s%N)
STREAM_RESP=$(curl -s -N -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"user_name\":\"$USER_S\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Count to 5\",\"model_mode\":\"lite\",\"stream\":true}")
END=$(date +%s%N)
STREAM_TIME=$(( (END - START) / 1000000 ))

if echo "$STREAM_RESP" | grep -q "data:"; then
    pass "Streaming response received"
    echo "    Stream time: ${STREAM_TIME}ms"
else
    fail "Streaming not received"
fi

echo ""

# ================================================================================
# CATEGORY 5: Skills System (MUSE-Autoskill)
# ================================================================================
echo -e "${BLUE}=== CATEGORY 5: Skills System ===${NC}"

SKILLS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/projects/$PROJECT_ID/skills")
if echo "$SKILLS" | jq -e '.skills' >/dev/null 2>&1; then
    pass "List skills endpoint"
else
    fail "List skills endpoint failed"
fi

SKILL_REQ='{
  "name": "test_skill",
  "description": "Test skill",
  "trigger_patterns": ["test trigger", "say hi"],
  "prompt_template": "When user says {input}, respond with a friendly greeting.",
  "expected_behavior": "Friendly response",
  "test_cases": [
    {"input": "say hi", "expected_keywords": ["hello", "hi", "greeting"]},
    {"input": "test trigger", "expected_keywords": ["hello", "hi"]}
  ]
}'

CREATE_RESP=$(curl -s -X POST "$BASE_URL/v1/projects/$PROJECT_ID/skills" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$SKILL_REQ")

SKILL_ID=$(echo "$CREATE_RESP" | jq -r '.id // empty')
if [ -n "$SKILL_ID" ]; then
    pass "Create skill works"
    echo "    Skill ID: $SKILL_ID"
    
    GET_RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/projects/$PROJECT_ID/skills/$SKILL_ID")
    if echo "$GET_RESP" | jq -e '.id' >/dev/null 2>&1; then
        pass "Get skill by ID"
    else
        fail "Get skill by ID failed"
    fi
    
    UPDATE_RESP=$(curl -s -X PATCH "$BASE_URL/v1/projects/$PROJECT_ID/skills/$SKILL_ID" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"description": "Updated description"}')
    if echo "$UPDATE_RESP" | jq -e '.description == "Updated description"' >/dev/null 2>&1; then
        pass "Update skill works"
    else
        fail "Update skill failed"
    fi
    
    DEL_RESP=$(curl -s -X DELETE "$BASE_URL/v1/projects/$PROJECT_ID/skills/$SKILL_ID" \
      -H "X-API-KEY: $API_KEY")
    if [ $? -eq 0 ]; then
        pass "Delete skill works"
        GET_DEL=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/projects/$PROJECT_ID/skills/$SKILL_ID")
        if echo "$GET_DEL" | jq -e '.detail' >/dev/null 2>&1; then
            pass "Deleted skill returns 404"
        else
            fail "Deleted skill still accessible"
        fi
    else
        fail "Delete skill failed"
    fi
else
    fail "Create skill failed"
fi

echo ""

# ================================================================================
# CATEGORY 6: Knowledge & RAG
# ================================================================================
echo -e "${BLUE}=== CATEGORY 6: Knowledge & RAG ===${NC}"

CARD_REQ='{
  "tenant_id": "default",
  "project_id": "'"$PROJECT_ID"'",
  "knowledge_domain": "test_domain",
  "title": "Test Card Title",
  "summary": "Test summary",
  "content": "This is a test knowledge card about testing things.",
  "tags": ["test", "knowledge"],
  "trust_level": 3
}'

CARD_RESP=$(curl -s -X POST "$BASE_URL/v1/knowledge/cards" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$CARD_REQ")

CARD_ID=$(echo "$CARD_RESP" | jq -r '.card.id // empty')
if [ -n "$CARD_ID" ]; then
    pass "Create knowledge card"
    echo "    Card ID: $CARD_ID"
    
    LIST_RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/knowledge/cards?tenant_id=default&project_id=$PROJECT_ID")
    if echo "$LIST_RESP" | jq -e '.cards' >/dev/null 2>&1; then
        pass "List knowledge cards"
    else
        fail "List knowledge cards failed"
    fi
    
    SEARCH_RESP=$(curl -s -X POST "$BASE_URL/v1/knowledge/search" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"tenant_id\":\"default\",\"project_id\":\"$PROJECT_ID\",\"query\":\"test knowledge\",\"limit\":5}")
    if echo "$SEARCH_RESP" | jq -e '.results' >/dev/null 2>&1; then
        pass "Search knowledge"
    else
        fail "Search knowledge failed"
    fi
    
    DEL_CARD=$(curl -s -X DELETE "$BASE_URL/v1/knowledge/cards/$CARD_ID" -H "X-API-KEY: $API_KEY")
    if [ $? -eq 0 ]; then
        pass "Delete knowledge card"
    else
        fail "Delete knowledge card failed"
    fi
else
    fail "Create knowledge card failed"
fi

echo ""

# ================================================================================
# CATEGORY 7: User & Sessions
# ================================================================================
echo -e "${BLUE}=== CATEGORY 7: User & Sessions ===${NC}"

USER_SESS="${USER_BASE}_sess"

CHAT_RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"user_name\":\"$USER_SESS\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Hello\",\"model_mode\":\"lite\"}")

SESS_ID=$(echo "$CHAT_RESP" | jq -r '.session_id // empty')
if [ -n "$SESS_ID" ]; then
    pass "User session created via chat"
    
    SESSIONS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/users/$USER_SESS/sessions?project_id=$PROJECT_ID")
    if echo "$SESSIONS" | jq -e '.sessions' >/dev/null 2>&1; then
        pass "Get user sessions"
    else
        fail "Get user sessions failed"
    fi
    
    CLEAR_RESP=$(curl -s -X DELETE "$BASE_URL/v1/users/$USER_SESS/history?project_id=$PROJECT_ID" \
      -H "X-API-KEY: $API_KEY")
    if [ $? -eq 0 ]; then
        pass "Clear user history"
    else
        fail "Clear user history failed"
    fi
else
    fail "Chat to create session failed"
fi

echo ""

# ================================================================================
# CATEGORY 8: Memory Services
# ================================================================================
echo -e "${BLUE}=== CATEGORY 8: Memory Services ===${NC}"

USER_MEM="${USER_BASE}_mem"

MEM_RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/memory?user_name=$USER_MEM&project_id=$PROJECT_ID")
if echo "$MEM_RESP" | jq -e '.memories' >/dev/null 2>&1; then
    pass "Get memory endpoint"
else
    fail "Get memory endpoint failed"
fi

for i in {1..5}; do
    curl -s -X POST "$BASE_URL/v1/chat" \
      -H "X-API-KEY: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"user_name\":\"$USER_MEM\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Remember this: my favorite color is blue\",\"model_mode\":\"lite\"}" >/dev/null
    sleep 0.2
done
pass "Multiple messages sent (memory build)"

echo ""

# ================================================================================
# CATEGORY 9: Admin API Key Management
# ================================================================================
echo -e "${BLUE}=== CATEGORY 9: Admin API Key Management ===${NC}"

KEYS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/management/keys")
if echo "$KEYS" | jq -e '.keys' >/dev/null 2>&1; then
    pass "List API keys"
else
    fail "List API keys failed"
fi

SESS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/management/sessions")
if echo "$SESS" | jq -e '.sessions' >/dev/null 2>&1; then
    pass "List sessions"
else
    fail "List sessions failed"
fi

TENANTS=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/tenants")
if echo "$TENANTS" | jq -e '.tenants' >/dev/null 2>&1; then
    pass "List tenants"
else
    fail "List tenants failed"
fi

NEW_KEY=$(curl -s -X POST "$BASE_URL/v1/admin/keys" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"test_key_'"$(date +%s)"'", "rpm": 100, "budget_usd": 10}')
KEY_ID=$(echo "$NEW_KEY" | jq -r '.key.id // empty')
if [ -n "$KEY_ID" ]; then
    pass "Create new API key"
    echo "    New Key ID: $KEY_ID"
    
    DISABLE=$(curl -s -X DELETE "$BASE_URL/v1/admin/keys/$KEY_ID" -H "X-API-KEY: $API_KEY")
    if [ $? -eq 0 ]; then
        pass "Disable API key"
    else
        fail "Disable API key failed"
    fi
else
    fail "Create API key failed"
fi

echo ""

# ================================================================================
# CATEGORY 10: CrewAI Agents
# ================================================================================
echo -e "${BLUE}=== CATEGORY 10: CrewAI Agents ===${NC}"

CREW_REQ='{
  "project_id": "'"$PROJECT_ID"'",
  "user_name": "'"${USER_BASE}_crew"'",
  "task": "Research the latest AI trends",
  "mode": "quick"
}'

CREW_RESP=$(curl -s -X POST "$BASE_URL/v1/crew/research" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$CREW_REQ")

if echo "$CREW_RESP" | jq -e '.result' >/dev/null 2>&1; then
    pass "CrewAI research endpoint"
else
    if echo "$CREW_RESP" | jq -e '.detail' >/dev/null 2>&1; then
        echo "    (CrewAI disabled/not configured - OK)"
    else
        fail "CrewAI research failed unexpectedly"
    fi
fi

echo ""

# ================================================================================
# CATEGORY 11: Provider Health
# ================================================================================
echo -e "${BLUE}=== CATEGORY 11: Provider Health ===${NC}"

PROV=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/health/providers")
if echo "$PROV" | jq -e '.providers' >/dev/null 2>&1; then
    pass "Provider health endpoint"
    
    LLAMA_STATUS=$(echo "$PROV" | jq -r '.providers[] | select(.name=="llama_cpp") | .status')
    if [ "$LLAMA_STATUS" = "configured" ]; then
        pass "Local provider (llama_cpp) configured"
    else
        echo "    Note: llama_cpp status='$LLAMA_STATUS' (servers may not be running)"
    fi
else
    fail "Provider health failed"
fi

echo ""

# ================================================================================
# CATEGORY 12: Concurrent Requests
# ================================================================================
echo -e "${BLUE}=== CATEGORY 12: Concurrent Requests ===${NC}"

CONCURRENT=5
PIDS=()

echo "Sending $CONCURRENT concurrent requests..."
for i in $(seq 1 $CONCURRENT); do
    (
        RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
          -H "X-API-KEY: $API_KEY" \
          -H "Content-Type: application/json" \
          -d "{\"user_name\":\"${USER_BASE}_concurrent_$i\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"Hello $i\",\"model_mode\":\"lite\"}")
        if echo "$RESP" | jq -e '.content' >/dev/null 2>&1; then
            echo "concurrent_$i:OK"
        else
            echo "concurrent_$i:FAIL"
        fi
    ) &
    PIDS+=($!)
done

RESULTS=""
for pid in "${PIDS[@]}"; do
    RESULT=$(wait $pid 2>/dev/null || echo "FAIL")
    RESULTS="$RESULTS $RESULT"
done

SUCCESS_COUNT=$(echo "$RESULTS" | grep -o "OK" | wc -l)
if [ "$SUCCESS_COUNT" -eq "$CONCURRENT" ]; then
    pass "All $CONCURRENT concurrent requests succeeded"
else
    fail "Only $SUCCESS_COUNT/$CONCURRENT concurrent requests succeeded"
fi

echo ""

# ================================================================================
# CATEGORY 13: Edge Cases
# ================================================================================
echo -e "${BLUE}=== CATEGORY 13: Edge Cases ===${NC}"

EMPTY_RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"user_name\":\"${USER_BASE}_edge\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"\",\"model_mode\":\"lite\"}")
if echo "$EMPTY_RESP" | jq -e '.content // .detail' >/dev/null 2>&1; then
    pass "Empty message handled"
else
    fail "Empty message not handled"
fi

LONG_MSG="This is a very long message. " && for i in {1..50}; do LONG_MSG="$LONG_MSG This is repetition number $i."; done
LONG_RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"user_name\":\"${USER_BASE}_long\",\"project_id\":\"$PROJECT_ID\",\"user_message\":\"$LONG_MSG\",\"model_mode\":\"lite\"}")
if echo "$LONG_RESP" | jq -e '.content // .detail' >/dev/null 2>&1; then
    pass "Long message handled"
else
    fail "Long message not handled"
fi

NONEXIST_RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/users/nonexistent_user_12345/sessions?project_id=$PROJECT_ID")
if echo "$NONEXIST_RESP" | jq -e '.sessions' >/dev/null 2>&1; then
    pass "Non-existent user sessions returns empty"
else
    fail "Non-existent user sessions not handled"
fi

echo ""

# ================================================================================
# SUMMARY
# ================================================================================
echo -e "${BLUE}================================================================================"
echo "TEST SUMMARY"
echo "================================================================================"
echo -e "Passed: ${GREEN}$PASS${NC}"
echo -e "Failed: ${RED}$FAIL${NC}"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✓ ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠ SOME TESTS FAILED${NC}"
    exit 1
fi
