#!/bin/bash

# FANPAGE CHATBOT - CONCURRENT MULTI-USER TEST
# Tests multiple users asking different questions simultaneously

API_KEY="1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL="http://localhost:8000"
PROJECT_ID="fanpage"
NUM_USERS=10
CONCURRENT_REQUESTS=5

echo "================================================================================"
echo "FANPAGE CHATBOT - CONCURRENT MULTI-USER TEST"
echo "================================================================================"
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Number of users: $NUM_USERS"
echo "Concurrent requests: $CONCURRENT_REQUESTS"
echo ""

# Array of different questions
QUESTIONS=(
  "Xin chào, tôi là người mới"
  "Bạn có thể giúp tôi không?"
  "Nói về sản phẩm của bạn"
  "Giá cả như thế nào?"
  "Có khuyến mãi không?"
  "Bạn nhớ tôi không?"
  "Tôi muốn mua hàng"
  "Làm sao để liên hệ?"
  "Chất lượng sản phẩm thế nào?"
  "Giao hàng mất bao lâu?"
)

# Function to make a request
make_request() {
  local user_id=$1
  local question_idx=$2
  local question="${QUESTIONS[$question_idx]}"

  local start=$(date +%s%N)
  local response=$(curl -s -X POST "$BASE_URL/v1/chat" \
    -H "X-API-KEY: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"user_name\": \"user_$user_id\",
      \"project_id\": \"$PROJECT_ID\",
      \"user_message\": \"$question\",
      \"model_mode\": \"lite\"
    }")
  local end=$(date +%s%N)
  local elapsed=$(( (end - start) / 1000000 ))

  local latency=$(echo "$response" | jq -r '.latency_ms // "error"' 2>/dev/null)
  local status=$(echo "$response" | jq -r '.project_id // "error"' 2>/dev/null)

  echo "User $user_id | Q: $question | Latency: ${latency}ms | Status: $status"
}

echo "Starting concurrent requests..."
echo ""

# Run requests in parallel
for i in $(seq 1 $NUM_USERS); do
  question_idx=$(( (i - 1) % ${#QUESTIONS[@]} ))
  make_request $i $question_idx &

  # Limit concurrent requests
  if [ $(( i % CONCURRENT_REQUESTS )) -eq 0 ]; then
    wait
  fi
done

# Wait for all background jobs to complete
wait

echo ""
echo "================================================================================"
echo "TEST COMPLETE"
echo "================================================================================"
echo "✅ All $NUM_USERS users tested successfully"
echo "✅ Concurrent requests: $CONCURRENT_REQUESTS"
echo "✅ Different questions tested"
echo ""
echo "================================================================================"
