#!/bin/bash

# FANPAGE CHATBOT - HEAVY LOAD TEST
# Tests system under heavy concurrent load with multiple users

API_KEY="1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8"
BASE_URL="http://localhost:8000"
PROJECT_ID="fanpage"
NUM_USERS=20
REQUESTS_PER_USER=3
CONCURRENT_LIMIT=10

echo "================================================================================"
echo "FANPAGE CHATBOT - HEAVY LOAD TEST"
echo "================================================================================"
echo "Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Number of users: $NUM_USERS"
echo "Requests per user: $REQUESTS_PER_USER"
echo "Total requests: $(( NUM_USERS * REQUESTS_PER_USER ))"
echo "Concurrent limit: $CONCURRENT_LIMIT"
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
  "Có bảo hành không?"
  "Thanh toán như thế nào?"
  "Có hỗ trợ 24/7 không?"
  "Sản phẩm có sẵn không?"
  "Tôi muốn hủy đơn hàng"
)

# Tracking variables
TOTAL_REQUESTS=0
SUCCESSFUL_REQUESTS=0
FAILED_REQUESTS=0
TOTAL_LATENCY=0
MIN_LATENCY=999999
MAX_LATENCY=0

# Function to make a request
make_request() {
  local user_id=$1
  local request_num=$2
  local question_idx=$3
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

  local latency=$(echo "$response" | jq -r '.latency_ms // 0' 2>/dev/null)
  local status=$(echo "$response" | jq -r '.project_id // "error"' 2>/dev/null)

  if [ "$status" = "fanpage" ]; then
    echo "✅ User $user_id | Req $request_num | Q: ${question:0:30}... | Latency: ${latency}ms"
    SUCCESSFUL_REQUESTS=$(( SUCCESSFUL_REQUESTS + 1 ))
    TOTAL_LATENCY=$(echo "$TOTAL_LATENCY + $latency" | bc)

    # Update min/max
    if (( $(echo "$latency < $MIN_LATENCY" | bc -l) )); then
      MIN_LATENCY=$latency
    fi
    if (( $(echo "$latency > $MAX_LATENCY" | bc -l) )); then
      MAX_LATENCY=$latency
    fi
  else
    echo "❌ User $user_id | Req $request_num | Error: $status"
    FAILED_REQUESTS=$(( FAILED_REQUESTS + 1 ))
  fi

  TOTAL_REQUESTS=$(( TOTAL_REQUESTS + 1 ))
}

echo "Starting heavy load test..."
echo ""

# Run requests in parallel with limit
for user_id in $(seq 1 $NUM_USERS); do
  for req_num in $(seq 1 $REQUESTS_PER_USER); do
    question_idx=$(( (user_id + req_num - 2) % ${#QUESTIONS[@]} ))
    make_request $user_id $req_num $question_idx &

    # Limit concurrent requests
    if [ $(jobs -r -p | wc -l) -ge $CONCURRENT_LIMIT ]; then
      wait -n
    fi
  done
done

# Wait for all background jobs to complete
wait

echo ""
echo "================================================================================"
echo "LOAD TEST RESULTS"
echo "================================================================================"
echo "Total Requests:      $TOTAL_REQUESTS"
echo "Successful:          $SUCCESSFUL_REQUESTS"
echo "Failed:              $FAILED_REQUESTS"
echo "Success Rate:        $(( SUCCESSFUL_REQUESTS * 100 / TOTAL_REQUESTS ))%"
echo ""

if [ $SUCCESSFUL_REQUESTS -gt 0 ]; then
  AVG_LATENCY=$(echo "scale=2; $TOTAL_LATENCY / $SUCCESSFUL_REQUESTS" | bc)
  echo "Latency Statistics:"
  echo "  Min:               ${MIN_LATENCY}ms"
  echo "  Max:               ${MAX_LATENCY}ms"
  echo "  Average:           ${AVG_LATENCY}ms"
fi

echo ""
echo "================================================================================"
echo "✅ Heavy load test complete!"
echo "================================================================================"
