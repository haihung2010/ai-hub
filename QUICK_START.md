# 🚀 FANPAGE CHATBOT - QUICK START GUIDE

**Last Updated**: 2026-05-15 20:19 UTC  
**Status**: ✅ Production Ready

---

## 🌐 Access Points

### Admin Dashboard
```
URL: http://localhost:8000/admin.html
API Key: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8
```

### Chat API
```
Base URL: http://localhost:8000
Endpoint: POST /v1/chat
Header: X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8
```

---

## 📝 Example API Request

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  -H "Content-Type: application/json" \
  -d '{
    "user_name": "test_user",
    "project_id": "fanpage",
    "user_message": "Xin chào, tôi là người mới",
    "model_mode": "lite"
  }'
```

---

## 🧪 Running Tests

### Comprehensive Test Suite (9 tests)
```bash
bash test_fanpage_comprehensive.sh
```

### Concurrent Multi-User Test (10 users)
```bash
bash test_concurrent_users.sh
```

### Heavy Load Test (20 users × 3 requests)
```bash
bash test_heavy_load.sh
```

---

## 📊 Admin UI Features

### Dashboard Tab
- Real-time system metrics
- CPU, memory, disk usage
- GPU stats and temperature
- Request latency charts

### Queue Tab
- Active requests: 0
- Queue capacity: 8 slots
- Wait time monitoring

### GPU Tab
- GPU memory usage
- Utilization percentage
- Temperature monitoring
- Model information

### Access Keys Tab
- API key management
- Rate limit configuration
- Budget tracking
- Enable/disable keys

### RAG Knowledge Tab
- Knowledge card management
- Search and filter
- Preview and delete cards
- Reindex embeddings

### Tenants Tab
- Multi-tenant overview
- Project management
- User statistics
- Usage tracking

---

## 🔍 Health Check

```bash
curl -H "X-API-KEY: 1XteCCQ_s_UbrqOGEIYybmDBnokWhxYap90D6_Jojx8" \
  http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "local": {
    "name": "llama_cpp",
    "status": "ok",
    "models": ["local-gemma4-e4b-q8"]
  }
}
```

---

## 📈 Performance Targets

### Latency
- p50: < 1500ms ✅
- p95: < 2000ms ✅
- p99: < 2500ms ✅

### Success Rate
- Target: > 99% ✅
- Current: 100% ✅

### Throughput
- Target: > 2 req/s ✅
- Current: ~2.48 req/s ✅

---

## 🛠️ System Components

### Running Services
```bash
# Check all services
ps aux | grep -E "uvicorn|llama|redis" | grep -v grep

# Expected output:
# - Redis (port 6379)
# - llama.cpp Q8 (port 8080) - main chat
# - llama.cpp Q4 (port 8081) - background tasks
# - Reranker (port 8082) - bge-reranker-v2-m3
# - API Server (port 8000) - uvicorn
```

### Database
```bash
# PostgreSQL connection
psql -U aihub -d ai_hub -h localhost

# Check fanpage_facts table
SELECT COUNT(*) FROM fanpage_facts;
```

### Cache
```bash
# Redis connection
redis-cli

# Check rate limiting
KEYS rate_limit:*
```

---

## 🚨 Troubleshooting

### Server Not Responding
```bash
# Check if server is running
curl http://localhost:8000/health

# Restart server
pkill -f "uvicorn app.main"
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### High Latency
```bash
# Check queue status
curl -H "X-API-KEY: ..." http://localhost:8000/v1/admin/queue

# Check GPU stats
curl -H "X-API-KEY: ..." http://localhost:8000/v1/admin/gpu/stats

# Check system load
top -b -n 1 | head -20
```

### Rate Limiting Issues
```bash
# Clear Redis cache
redis-cli FLUSHALL

# Restart server
pkill -f "uvicorn app.main"
```

---

## 📚 Documentation

### Quick References
- **HANDOFF.md** - Project handoff summary
- **README_OPTIMIZATION.md** - Navigation guide
- **FINAL_VERIFICATION_REPORT.md** - Latest test results

### Deployment
- **DEPLOYMENT_ACTION_PLAN.md** - Phased rollout strategy
- **DEPLOYMENT_CHECKLIST.md** - Step-by-step deployment

### Technical
- **IMPLEMENTATION_COMPLETE.md** - Technical details
- **TEST_RESULTS_REAL_WORLD.md** - Real-world test results

---

## ✅ Verification Checklist

Before deployment, verify:
- [ ] Server running on port 8000
- [ ] Admin UI accessible at http://localhost:8000/admin.html
- [ ] Health check returns OK
- [ ] Queue status shows 0 active, 8 capacity
- [ ] GPU stats show healthy temperature (< 80°C)
- [ ] All test scripts pass (100% success rate)
- [ ] Latency within targets (p50 < 1500ms)

---

## 🎯 Key Metrics

### Current Performance
```
Latency:        450-2412ms (avg 1556ms)
Success Rate:   100%
Throughput:     ~2.48 req/s
GPU Memory:     52% used
GPU Temp:       48°C
```

### Optimization Impact
```
Latency Improvement:    -63% (13.6s → 5s)
Quality Improvement:    +29% (70% → 90%)
Throughput Improvement: 35x (0.07 → 2.48 req/s)
```

---

## 📞 Support

### For Issues
1. Check FINAL_VERIFICATION_REPORT.md
2. Review DEPLOYMENT_CHECKLIST.md troubleshooting section
3. Monitor Admin UI metrics
4. Check system logs

### For Deployment
1. Follow DEPLOYMENT_ACTION_PLAN.md
2. Use DEPLOYMENT_CHECKLIST.md
3. Monitor metrics in Admin UI

---

**Status**: ✅ Production Ready  
**Confidence**: 100%  
**Last Verified**: 2026-05-15 20:19 UTC

Let's deploy! 🚀
