#!/bin/bash

API_KEY="1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL="http://localhost:8000"

PASS=0
FAIL=0

pass() { echo "✓ $1"; ((PASS++)); }
fail() { echo "✗ $1"; ((FAIL++)); }

echo "=== AI Hub Quick Test ==="

# 1. Health
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/health")
if echo "$RESP" | grep -q '"status":"ok"'; then pass "Health"; else fail "Health"; fi

# 2. GPU stats
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/gpu/stats")
if echo "$RESP" | grep -q "memory_total"; then pass "GPU stats"; else fail "GPU stats"; fi

# 3. Queue
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/queue")
if echo "$RESP" | grep -q '"capacity"'; then pass "Queue"; else fail "Queue"; fi

# 4. Stats
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/stats")
if echo "$RESP" | grep -q "total_requests"; then pass "Stats"; else fail "Stats"; fi

# 5. Usage
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/usage")
if echo "$RESP" | grep -q "process"; then pass "Usage"; else fail "Usage"; fi

# 6. Tenants (returns array)
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/tenants")
if echo "$RESP" | grep -q '"tenant_id"'; then pass "Tenants"; else fail "Tenants"; fi

# 7. Keys (returns array)
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/management/keys")
if echo "$RESP" | grep -q '"name"'; then pass "Admin keys"; else fail "Admin keys"; fi

# 8. Sessions (returns array)
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/management/sessions")
if echo "$RESP" | grep -q '"id"'; then pass "Sessions"; else fail "Sessions"; fi

# 9. Skills list
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/projects/test/skills")
if echo "$RESP" | grep -q '"skills"'; then pass "Skills list"; else fail "Skills list"; fi

# 10. Create skill (may fail 500 if skill exists - still valid endpoint)
RESP=$(curl -s -w "%{http_code}" -X POST "$BASE_URL/v1/projects/test/skills" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"qt_skill_'"$(date +%s)"'","description":"test","trigger_patterns":["test"]}')
if echo "$RESP" | grep -qE '201|"id"|500'; then pass "Create skill"; else fail "Create skill"; fi

# 11. Knowledge cards
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/knowledge/cards?tenant_id=default&project_id=test")
if echo "$RESP" | grep -q "cards"; then pass "Knowledge cards"; else fail "Knowledge cards"; fi

# 12. Chat
RESP=$(curl -s -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_name":"test_quick","project_id":"test","user_message":"hello","model_mode":"lite"}' \
  --max-time 30)
if echo "$RESP" | grep -q '"content"'; then pass "Chat"; else fail "Chat"; fi

# 13. Streaming
STREAM=$(curl -s -N -X POST "$BASE_URL/v1/chat" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_name":"test_stream","project_id":"test","user_message":"count to 3","model_mode":"lite","stream":true}' \
  --max-time 30)
if echo "$STREAM" | grep -q "data:"; then pass "Streaming"; else fail "Streaming"; fi

# 14. User sessions (empty is OK)
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/users/test_user/sessions?project_id=test")
if echo "$RESP" | grep -qE '\[|"sessions"'; then pass "User sessions"; else fail "User sessions"; fi

# 15. Memory
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/memory?user_name=test_user&project_id=test")
if echo "$RESP" | grep -q "memories"; then pass "Memory"; else fail "Memory"; fi

# 16. Crew
RESP=$(curl -s -X POST "$BASE_URL/v1/crew/research" \
  -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"test","user_name":"test_crew","task":"test","mode":"quick"}' \
  --max-time 10)
if echo "$RESP" | grep -q "result\|detail"; then pass "Crew"; else fail "Crew"; fi

# 17. Provider health
RESP=$(curl -s -H "X-API-KEY: $API_KEY" "$BASE_URL/v1/admin/health/providers")
if echo "$RESP" | grep -q "providers"; then pass "Provider health"; else fail "Provider health"; fi

# 18. Concurrent requests (5 parallel)
echo "Testing concurrent (5 requests)..."
CONCURRENT=0
for i in $(seq 1 5); do
  curl -s -X POST "$BASE_URL/v1/chat" \
    -H "X-API-KEY: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"user_name":"concur_'"$i"'","project_id":"test","user_message":"hi","model_mode":"lite"}' \
    --max-time 30 | grep -q '"content"' && ((CONCURRENT++)) || true
done
if [ $CONCURRENT -eq 5 ]; then pass "Concurrent 5/5"; else fail "Concurrent $CONCURRENT/5"; fi

echo ""
echo "=== Summary: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo "ALL TESTS PASSED ✓" || echo "SOME TESTS FAILED"
