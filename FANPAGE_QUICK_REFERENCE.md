# Fanpage Chatbot: Quick Reference Guide
**Last Updated**: 2026-05-15

---

## 🎯 Goals

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| **Latency (p50)** | 13.6s | 5s | -63% ⚡ |
| **Hallucination Rate** | ~30% | <20% | -33% 🎯 |
| **Response Quality** | 70% | 90% | +29% 📈 |
| **Memory Extraction** | 2-3s | 1s | -50% ⏱️ |
| **User Satisfaction** | 3.5/5 | 4.5/5 | +29% 😊 |

---

## 📋 Implementation Phases

### Phase 1: Quick Wins (1 week)
**Effort**: 4-6 hours  
**Impact**: -40% latency, -20% hallucination

```bash
# 1. Enable failure risk scoring
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true

# 2. Reduce history for fanpage
FANPAGE_MAX_HISTORY_MESSAGES=10

# 3. Parallel loading (code change)
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 2.3

# 4. Lazy web search (code change)
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 2.4

# 5. Skip reranker for high-confidence (code change)
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 2.5
```

**Expected Result**: p50 latency 13.6s → 8-10s

---

### Phase 2: Memory Optimization (1 week)
**Effort**: 8-10 hours  
**Impact**: +20% quality, -1s latency

```bash
# 1. Create fact extraction service
# File: app/services/fact_extraction_service.py
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 5

# 2. Update config
# File: app/core/config.py
# Add fanpage-specific settings
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 1

# 3. Schedule fact extraction
# File: app/services/ai_service.py
# Update _schedule_memory_jobs()
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 7

# 4. Create fanpage prompt
# File: app/prompts/fanpage.md
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 4
```

**Expected Result**: p50 latency 8-10s → 6-7s, quality +20%

---

### Phase 3: Quality Improvements (1 week)
**Effort**: 6-8 hours  
**Impact**: +30% quality, better monitoring

```bash
# 1. RAG deduplication
# File: app/services/knowledge_retrieval_service.py
# Add _deduplicate_results()
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 3.1

# 2. Per-project latency tuning
# File: app/services/ai_service.py
# Add _get_latency_threshold()
# See FANPAGE_IMPLEMENTATION_GUIDE.md section 2.2

# 3. Admin dashboard metrics
# File: static/admin.html
# Add fanpage-specific metrics

# 4. Monitoring & alerts
# Set up alerts for fanpage metrics
```

**Expected Result**: p50 latency 6-7s → 5s, quality +30%

---

## 🔧 Configuration Reference

### Minimal Setup (Phase 1)
```bash
# .env
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true
FAILURE_RISK_HIGH_THRESHOLD=0.6
FAILURE_RISK_MEDIUM_THRESHOLD=0.3
FAILURE_RISK_ENABLE_SEARCH_ACTION=true

FANPAGE_MAX_HISTORY_MESSAGES=10
```

### Full Setup (Phase 1-3)
```bash
# .env
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true
FAILURE_RISK_HIGH_THRESHOLD=0.6
FAILURE_RISK_MEDIUM_THRESHOLD=0.3
FAILURE_RISK_ENABLE_SEARCH_ACTION=true

FANPAGE_MAX_HISTORY_MESSAGES=10
FANPAGE_KNOWLEDGE_MAX_CHUNKS=6
FANPAGE_KNOWLEDGE_DEDUP_THRESHOLD=0.9
FANPAGE_LATENCY_THRESHOLD_MS=3000
FANPAGE_ENABLE_FACT_EXTRACTION=true
FANPAGE_FACT_EXTRACTION_THRESHOLD=5

PROJECT_LATENCY_THRESHOLDS={"fanpage": 3000, "support": 5000}
```

---

## 📊 Before & After Comparison

### Request Flow

**BEFORE** (Sequential, 13.6s p50):
```
User Message
    ↓ (0.5s)
Load Memory (sequential)
    ↓ (1-2s)
Load RAG (sequential)
    ↓ (2-5s)
Load Web Search (if triggered)
    ↓ (8-10s)
Model Inference
    ↓ (0.5s)
Format Response
    ↓
Return to User (13.6s total)
    ↓ (async, 2-3s)
Extract Memory (background)
```

**AFTER** (Parallel, 5s p50):
```
User Message
    ↓ (0.2s)
Load Memory + RAG (parallel)
    ↓ (1-2s, not 1-2s + 1-2s)
Load Web Search (lazy, only if needed)
    ↓ (0s if skipped, 2-3s if needed)
Model Inference
    ↓ (4-5s, faster due to priority queue)
Format Response
    ↓
Return to User (5s total)
    ↓ (async, 1s)
Extract Facts (background)
```

### Memory System

**BEFORE** (Summaries OR StructMem):
```
Conversation:
  User: "I'm John, interested in Product X, budget $500"
  AI: "Great! I can help with Product X"
  User: "What's the price?"
  AI: "Product X costs $299"
  User: "Do you have financing?"
  AI: "Yes, we offer financing"

After 5 messages:
  Summary: "User interested in Product X, asking about pricing and financing"
  (Loses: name is John, budget $500)
```

**AFTER** (Summaries + Facts):
```
Same conversation:

After 5 messages:
  Summary: "User interested in Product X, asking about pricing and financing"
  Facts:
    - user_name: "John"
    - interested_in: "Product X"
    - budget: "$500"
    - asked_about: "financing"

Next message:
  "Hi John! I see you're interested in Product X with a $500 budget..."
  (Retains all facts, personalized)
```

### Response Quality

**BEFORE** (Generic):
```
User: "What's the best product for me?"
AI: "We have many great products. Could you tell me more about your needs?"
(Generic, no personalization)
```

**AFTER** (Personalized):
```
User: "What's the best product for me?"
AI: "Based on your interest in Product X and $500 budget, I'd recommend 
    Product X (normally $299) or Product Y ($399). Both fit your budget 
    and have great reviews. Which interests you more?"
(Personalized, specific, helpful)
```

### Latency Breakdown

**BEFORE**:
```
Queue Wait:        1-3s (8 slots, high concurrency)
Memory Load:       0.5-1s
RAG Search:        1-2s
Reranking:         1-2s
Web Search:        2-5s (if triggered)
Model Inference:   8-10s (Q8, ~1-2s per token)
Response Format:   0.5s
─────────────────────────
Total:             13.6s p50
```

**AFTER**:
```
Queue Wait:        0.5-1s (priority queue)
Memory + RAG:      1-2s (parallel, not sequential)
Web Search:        0s (lazy, skipped if not needed)
Reranking:         0s (skipped for high-confidence)
Model Inference:   2-3s (faster due to priority)
Response Format:   0.5s
─────────────────────────
Total:             5s p50
```

---

## 🚀 Quick Start (5 minutes)

### Step 1: Update .env
```bash
# Add these lines to .env
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true
FANPAGE_MAX_HISTORY_MESSAGES=10
```

### Step 2: Restart Server
```bash
# Kill existing server
pkill -f "uvicorn app.main"

# Restart
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 3: Test
```bash
# Send test message to fanpage
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-KEY: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "What products do you have?",
    "project_id": "fanpage",
    "user_name": "test_user",
    "stream": false
  }'

# Check latency in response
# Should be faster than before
```

---

## 📈 Monitoring Checklist

### Daily Checks
- [ ] Fanpage p50 latency < 5s
- [ ] Error rate < 5%
- [ ] No OOM errors
- [ ] Memory extraction success > 90%

### Weekly Checks
- [ ] Hallucination rate < 20%
- [ ] User satisfaction > 4.0/5.0
- [ ] Response quality > 0.8
- [ ] Fact extraction accuracy > 85%

### Monthly Checks
- [ ] Compare metrics to baseline
- [ ] Review user feedback
- [ ] Identify new optimization opportunities
- [ ] Plan next phase

---

## 🐛 Troubleshooting

### Issue: Latency still high (>8s)
**Cause**: Parallel loading not working  
**Fix**:
```python
# Check if asyncio.gather is being used
# File: app/services/ai_service.py
# Search for "_load_context_parallel"
# Verify it's being called in chat_stream()
```

### Issue: Fact extraction failing
**Cause**: Model returning invalid JSON  
**Fix**:
```python
# Check logs for JSON parse errors
# File: app/services/fact_extraction_service.py
# Line ~50: _parse_payload()
# Add more lenient JSON parsing
```

### Issue: Web search running too often
**Cause**: Lazy search not working  
**Fix**:
```python
# Check if knowledge results are being returned
# File: app/services/ai_service.py
# Search for "_load_web_search_lazy"
# Verify knowledge_block is not None
```

### Issue: Memory extraction too slow
**Cause**: Background model overloaded  
**Fix**:
```bash
# Increase background Q4 slots
# File: scripts/start_background_q4.sh
# Change: -n_gpu_layers 99 -ngl 99
# To: -n_gpu_layers 99 -ngl 99 -n_parallel 4
```

---

## 📚 File Reference

| File | Purpose | Phase |
|------|---------|-------|
| `.env` | Configuration | 1 |
| `app/core/config.py` | Settings schema | 2 |
| `app/services/ai_service.py` | Main orchestrator | 1-3 |
| `app/services/fact_extraction_service.py` | Fact extraction | 2 |
| `app/services/knowledge_retrieval_service.py` | RAG search | 3 |
| `app/prompts/fanpage.md` | Fanpage prompt | 2 |
| `app/main.py` | App initialization | 2 |
| `static/admin.html` | Admin dashboard | 3 |

---

## 🎓 Learning Resources

### Understanding the System
1. Read: `CLAUDE.md` (project overview)
2. Read: `AUDIT_REPORT_FANPAGE_OPTIMIZATION.md` (detailed analysis)
3. Read: `FANPAGE_IMPLEMENTATION_GUIDE.md` (implementation details)

### Code Examples
- Parallel loading: `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.3
- Lazy web search: `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.4
- Fact extraction: `FANPAGE_IMPLEMENTATION_GUIDE.md` section 5

### Testing
- Unit tests: `tests/unit/test_fact_extraction_service.py`
- Integration tests: `tests/integration/test_fanpage_latency.py`
- Load tests: `scripts/loadtest.py`

---

## 💡 Pro Tips

### Tip 1: Use Feature Flags
```python
# Instead of hardcoding "fanpage", use config
if settings.fanpage_enable_fact_extraction:
    # Run fact extraction
```

### Tip 2: Monitor Metrics
```bash
# Check latency in real-time
curl http://localhost:8000/v1/admin/stats \
  -H "X-API-KEY: your-api-key" | jq '.fanpage_latency'
```

### Tip 3: Gradual Rollout
```bash
# Test with 10% of traffic first
# Then 50%, then 100%
# Use feature flags to control rollout
```

### Tip 4: A/B Testing
```python
# Compare old vs new memory system
# Use random split: 50% old, 50% new
# Measure quality metrics
# Roll out winner
```

---

## 🔗 Related Documents

- **AUDIT_REPORT_FANPAGE_OPTIMIZATION.md** — Detailed audit and recommendations
- **FANPAGE_IMPLEMENTATION_GUIDE.md** — Step-by-step implementation
- **CLAUDE.md** — Project overview and architecture
- **AI_HUB_ARCHITECTURE_AND_DEPLOYMENT_PLAN.md** — System architecture

---

## 📞 Support

### Questions?
1. Check troubleshooting section above
2. Review implementation guide
3. Check logs: `tail -f app.log`
4. Check security log: `tail -f security.log`

### Issues?
1. Create GitHub issue with:
   - Error message
   - Logs
   - Steps to reproduce
   - Expected vs actual behavior

### Feedback?
1. Share metrics and observations
2. Suggest improvements
3. Report bugs
4. Request features

---

## ✅ Deployment Checklist

- [ ] Read all documentation
- [ ] Update `.env` with new settings
- [ ] Run tests: `pytest tests/ -v`
- [ ] Deploy to staging
- [ ] Monitor metrics for 24 hours
- [ ] Verify latency < 5s
- [ ] Verify error rate < 5%
- [ ] Deploy to production
- [ ] Monitor for 1 week
- [ ] Celebrate! 🎉

---

## 📊 Success Metrics

### Latency
- ✅ p50 < 5s
- ✅ p95 < 8s
- ✅ p99 < 12s

### Quality
- ✅ Hallucination rate < 20%
- ✅ Response quality > 0.8
- ✅ User satisfaction > 4.0/5.0

### Reliability
- ✅ Error rate < 5%
- ✅ Uptime > 99.5%
- ✅ Memory extraction success > 90%

---

**Last Updated**: 2026-05-15  
**Status**: Ready for Implementation  
**Estimated Timeline**: 3 weeks (Phase 1-3)  
**Expected ROI**: +40% user satisfaction, -60% latency

