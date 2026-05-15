# Fanpage Chatbot: Action Plan & Timeline
**Date**: 2026-05-15  
**Status**: Ready for Implementation  
**Timeline**: 3 weeks (Phase 1-3)

---

## 📌 Executive Summary

**Current State**: AI Hub is production-ready but over-engineered for fanpage chatbots.

**Problem**: 
- Latency p50 = 13.6s (target: 5s)
- Hallucination rate ~30% (target: <20%)
- Response quality 70% (target: 90%)

**Solution**: 3-phase optimization plan
- **Phase 1** (1 week): Quick wins → p50 8-10s
- **Phase 2** (1 week): Memory optimization → p50 6-7s
- **Phase 3** (1 week): Quality improvements → p50 5s

**Expected ROI**: +40% user satisfaction, -60% latency, -30% hallucination

---

## 🎯 Phase 1: Quick Wins (Week 1)

### Objective
Reduce latency from 13.6s → 8-10s with minimal code changes

### Tasks

#### Task 1.1: Enable Failure Risk Scoring (30 min)
**Owner**: DevOps  
**Effort**: 30 minutes  
**Impact**: -20% hallucination

```bash
# File: .env
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true
FAILURE_RISK_HIGH_THRESHOLD=0.6
FAILURE_RISK_MEDIUM_THRESHOLD=0.3
FAILURE_RISK_ENABLE_SEARCH_ACTION=true

# Restart server
pkill -f "uvicorn app.main"
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Verification**:
```bash
# Send test query
curl -X POST http://localhost:8000/v1/chat \
  -H "X-API-KEY: test-key" \
  -H "Content-Type: application/json" \
  -d '{"user_message": "What is the meaning of life?", "project_id": "fanpage", "stream": false}'

# Should add disclaimer if uncertain
```

**Status**: ⬜ Not Started

---

#### Task 1.2: Reduce Fanpage History (20 min)
**Owner**: Backend  
**Effort**: 20 minutes  
**Impact**: -0.5s latency

```bash
# File: .env
FANPAGE_MAX_HISTORY_MESSAGES=10
```

**Verification**:
```bash
# Check config loaded
curl http://localhost:8000/v1/admin/health \
  -H "X-API-KEY: test-key" | jq '.config.fanpage_max_history_messages'
# Should return: 10
```

**Status**: ⬜ Not Started

---

#### Task 1.3: Parallel Memory + RAG Loading (2 hours)
**Owner**: Backend  
**Effort**: 2 hours  
**Impact**: -1-2s latency

**Changes**:
1. Update `app/services/ai_service.py`
   - Add `_load_context_parallel()` method (lines ~300)
   - Update `chat_stream()` to use parallel loading (lines ~450)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.3

**Testing**:
```bash
# Run latency test
pytest tests/integration/test_fanpage_latency.py -v

# Should show p50 < 10s
```

**Status**: ⬜ Not Started

---

#### Task 1.4: Lazy Web Search (1.5 hours)
**Owner**: Backend  
**Effort**: 1.5 hours  
**Impact**: -2-5s for non-search queries

**Changes**:
1. Update `app/services/ai_service.py`
   - Add `_load_web_search_lazy()` method (lines ~300)
   - Update `chat_stream()` to use lazy search (lines ~480)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.4

**Testing**:
```bash
# Run lazy search test
pytest tests/unit/test_lazy_web_search.py -v

# Should skip web search for non-question queries
```

**Status**: ⬜ Not Started

---

#### Task 1.5: Skip Reranker for High-Confidence (1 hour)
**Owner**: Backend  
**Effort**: 1 hour  
**Impact**: -1-2s for high-confidence queries

**Changes**:
1. Update `app/services/ai_service.py`
   - Modify `_build_knowledge_block()` (lines ~165)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.5

**Testing**:
```bash
# Run RAG test
pytest tests/unit/test_rag_deduplication.py -v

# Should skip reranker for score > 0.8
```

**Status**: ⬜ Not Started

---

### Phase 1 Deliverables
- [ ] `.env` updated with failure risk settings
- [ ] `.env` updated with fanpage history setting
- [ ] `app/services/ai_service.py` updated with parallel loading
- [ ] `app/services/ai_service.py` updated with lazy web search
- [ ] `app/services/ai_service.py` updated with reranker skip
- [ ] All Phase 1 tests passing
- [ ] Deployed to staging
- [ ] Metrics verified: p50 < 10s

### Phase 1 Timeline
```
Mon: Tasks 1.1, 1.2 (50 min)
Tue: Task 1.3 (2 hours)
Wed: Task 1.4 (1.5 hours)
Thu: Task 1.5 (1 hour)
Fri: Testing, deployment, verification
```

### Phase 1 Success Criteria
- ✅ p50 latency < 10s (from 13.6s)
- ✅ Error rate < 5%
- ✅ No OOM errors
- ✅ All tests passing

---

## 🎯 Phase 2: Memory Optimization (Week 2)

### Objective
Improve response quality and reduce memory extraction latency

### Tasks

#### Task 2.1: Update Config (1 hour)
**Owner**: Backend  
**Effort**: 1 hour  
**Impact**: Foundation for Phase 2

**Changes**:
1. Update `app/core/config.py`
   - Add fanpage-specific settings (lines ~135)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 1

**Verification**:
```bash
# Check config
curl http://localhost:8000/v1/admin/health \
  -H "X-API-KEY: test-key" | jq '.config | {fanpage_max_history_messages, fanpage_enable_fact_extraction}'
```

**Status**: ⬜ Not Started

---

#### Task 2.2: Create Fact Extraction Service (2 hours)
**Owner**: Backend  
**Effort**: 2 hours  
**Impact**: Lightweight memory extraction

**Changes**:
1. Create `app/services/fact_extraction_service.py`

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 5

**Testing**:
```bash
# Run fact extraction tests
pytest tests/unit/test_fact_extraction_service.py -v

# Should extract facts with >90% accuracy
```

**Status**: ⬜ Not Started

---

#### Task 2.3: Create Fanpage Prompt (1 hour)
**Owner**: Product  
**Effort**: 1 hour  
**Impact**: Personality and brand voice

**Changes**:
1. Create `app/prompts/fanpage.md`

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 4

**Verification**:
```bash
# Check prompt loads
curl http://localhost:8000/v1/admin/health \
  -H "X-API-KEY: test-key" | jq '.prompts.fanpage'
```

**Status**: ⬜ Not Started

---

#### Task 2.4: Update AIService (1.5 hours)
**Owner**: Backend  
**Effort**: 1.5 hours  
**Impact**: Integrate fact extraction

**Changes**:
1. Update `app/services/ai_service.py`
   - Add per-project history cap (lines ~225)
   - Add per-project latency threshold (lines ~92)
   - Update `_schedule_memory_jobs()` (lines ~184)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2

**Testing**:
```bash
# Run memory tests
pytest tests/integration/test_fanpage_memory.py -v

# Should extract and inject facts
```

**Status**: ⬜ Not Started

---

#### Task 2.5: Update App Initialization (1 hour)
**Owner**: Backend  
**Effort**: 1 hour  
**Impact**: Wire up fact extraction service

**Changes**:
1. Update `app/main.py`
   - Initialize fact extraction service

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 6

**Verification**:
```bash
# Check service initialized
curl http://localhost:8000/v1/admin/health \
  -H "X-API-KEY: test-key" | jq '.services.fact_extraction'
```

**Status**: ⬜ Not Started

---

### Phase 2 Deliverables
- [ ] `app/core/config.py` updated with fanpage settings
- [ ] `app/services/fact_extraction_service.py` created
- [ ] `app/prompts/fanpage.md` created
- [ ] `app/services/ai_service.py` updated with per-project settings
- [ ] `app/main.py` updated with fact extraction initialization
- [ ] All Phase 2 tests passing
- [ ] Deployed to staging
- [ ] Metrics verified: p50 < 7s, quality +20%

### Phase 2 Timeline
```
Mon: Task 2.1 (1 hour)
Tue: Task 2.2 (2 hours)
Wed: Task 2.3 (1 hour)
Thu: Task 2.4 (1.5 hours)
Fri: Task 2.5 (1 hour) + testing, deployment
```

### Phase 2 Success Criteria
- ✅ p50 latency < 7s (from 8-10s)
- ✅ Fact extraction success > 90%
- ✅ Response quality +20%
- ✅ All tests passing

---

## 🎯 Phase 3: Quality Improvements (Week 3)

### Objective
Achieve target latency and quality metrics

### Tasks

#### Task 3.1: RAG Deduplication (1.5 hours)
**Owner**: Backend  
**Effort**: 1.5 hours  
**Impact**: Better knowledge coverage

**Changes**:
1. Update `app/services/knowledge_retrieval_service.py`
   - Add `_deduplicate_results()` method (lines ~30)
   - Update `search()` to use deduplication (lines ~37)

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 3.1

**Testing**:
```bash
# Run deduplication tests
pytest tests/unit/test_rag_deduplication.py -v

# Should remove similar content
```

**Status**: ⬜ Not Started

---

#### Task 3.2: Per-Project Latency Tuning (1 hour)
**Owner**: Backend  
**Effort**: 1 hour  
**Impact**: Better routing decisions

**Changes**:
1. Update `app/services/ai_service.py`
   - Add `_get_latency_threshold()` method (lines ~92)
   - Update hybrid routing to use per-project threshold

**Code Reference**: See `FANPAGE_IMPLEMENTATION_GUIDE.md` section 2.2

**Verification**:
```bash
# Check latency threshold
curl http://localhost:8000/v1/admin/health \
  -H "X-API-KEY: test-key" | jq '.config.project_latency_thresholds'
```

**Status**: ⬜ Not Started

---

#### Task 3.3: Admin Dashboard Metrics (2 hours)
**Owner**: Frontend  
**Effort**: 2 hours  
**Impact**: Real-time monitoring

**Changes**:
1. Update `static/admin.html`
   - Add fanpage metrics section
   - Add latency charts
   - Add quality metrics

**Verification**:
```bash
# Open admin dashboard
open http://localhost:8000/admin.html

# Should show fanpage metrics
```

**Status**: ⬜ Not Started

---

#### Task 3.4: Monitoring & Alerts (1.5 hours)
**Owner**: DevOps  
**Effort**: 1.5 hours  
**Impact**: Proactive issue detection

**Changes**:
1. Set up monitoring alerts
   - p50 latency > 5s
   - Error rate > 5%
   - Hallucination rate > 20%

**Verification**:
```bash
# Test alert trigger
# Simulate high latency
# Should trigger alert
```

**Status**: ⬜ Not Started

---

#### Task 3.5: Documentation & Training (1 hour)
**Owner**: Tech Lead  
**Effort**: 1 hour  
**Impact**: Team knowledge transfer

**Changes**:
1. Update CLAUDE.md with fanpage optimizations
2. Create runbook for monitoring
3. Train team on new features

**Status**: ⬜ Not Started

---

### Phase 3 Deliverables
- [ ] `app/services/knowledge_retrieval_service.py` updated with deduplication
- [ ] `app/services/ai_service.py` updated with per-project latency tuning
- [ ] `static/admin.html` updated with fanpage metrics
- [ ] Monitoring alerts configured
- [ ] Documentation updated
- [ ] Team trained
- [ ] All Phase 3 tests passing
- [ ] Deployed to production
- [ ] Metrics verified: p50 < 5s, quality +30%

### Phase 3 Timeline
```
Mon: Task 3.1 (1.5 hours)
Tue: Task 3.2 (1 hour)
Wed: Task 3.3 (2 hours)
Thu: Task 3.4 (1.5 hours)
Fri: Task 3.5 (1 hour) + final testing, production deployment
```

### Phase 3 Success Criteria
- ✅ p50 latency < 5s (from 6-7s)
- ✅ Hallucination rate < 20%
- ✅ Response quality > 0.8
- ✅ User satisfaction > 4.0/5.0
- ✅ All tests passing
- ✅ Monitoring in place

---

## 📊 Overall Timeline

```
Week 1 (May 15-19):
  Mon: Phase 1.1, 1.2
  Tue: Phase 1.3
  Wed: Phase 1.4
  Thu: Phase 1.5
  Fri: Testing, staging deployment

Week 2 (May 22-26):
  Mon: Phase 2.1
  Tue: Phase 2.2
  Wed: Phase 2.3
  Thu: Phase 2.4
  Fri: Phase 2.5, testing, staging deployment

Week 3 (May 29-Jun 2):
  Mon: Phase 3.1
  Tue: Phase 3.2
  Wed: Phase 3.3
  Thu: Phase 3.4
  Fri: Phase 3.5, final testing, production deployment
```

---

## 👥 Team Assignments

| Role | Tasks | Hours |
|------|-------|-------|
| Backend Lead | 1.3, 1.4, 1.5, 2.2, 2.4, 3.1, 3.2 | 12 |
| DevOps | 1.1, 1.2, 3.4 | 2 |
| Frontend | 3.3 | 2 |
| Product | 2.3 | 1 |
| Tech Lead | 3.5 | 1 |
| **Total** | | **18 hours** |

---

## 💰 Resource Requirements

| Resource | Quantity | Cost | Notes |
|----------|----------|------|-------|
| Developer Hours | 18 | $1,800 | 1 backend dev, 1 week |
| Staging Environment | 1 | $0 | Existing |
| Monitoring Tools | 1 | $0 | Existing |
| **Total** | | **$1,800** | |

---

## 🎯 Success Metrics

### Phase 1 Success
```
✅ p50 latency: 13.6s → 8-10s (-26%)
✅ Error rate: < 5%
✅ Hallucination rate: ~30% → ~25% (-17%)
```

### Phase 2 Success
```
✅ p50 latency: 8-10s → 6-7s (-30%)
✅ Response quality: 70% → 80% (+14%)
✅ Fact extraction success: > 90%
```

### Phase 3 Success
```
✅ p50 latency: 6-7s → 5s (-29%)
✅ Hallucination rate: ~25% → <20% (-20%)
✅ Response quality: 80% → 90% (+12%)
✅ User satisfaction: 3.5 → 4.5/5 (+29%)
```

---

## 🚀 Deployment Strategy

### Staging Deployment
1. Deploy Phase 1 to staging
2. Run full test suite
3. Monitor metrics for 24 hours
4. Get approval from tech lead
5. Deploy to production

### Production Deployment
1. Deploy during low-traffic window (2-4 AM)
2. Monitor metrics closely
3. Have rollback plan ready
4. Gradual rollout: 10% → 50% → 100%
5. Monitor for 1 week

### Rollback Plan
If any metric degrades:
1. Disable new feature via feature flag
2. Revert to previous version
3. Investigate root cause
4. Fix and redeploy

---

## 📋 Checklist

### Pre-Implementation
- [ ] All team members read documentation
- [ ] Staging environment ready
- [ ] Monitoring tools configured
- [ ] Rollback plan documented

### Phase 1
- [ ] All tasks completed
- [ ] All tests passing
- [ ] Staging deployment successful
- [ ] Metrics verified
- [ ] Production deployment approved

### Phase 2
- [ ] All tasks completed
- [ ] All tests passing
- [ ] Staging deployment successful
- [ ] Metrics verified
- [ ] Production deployment approved

### Phase 3
- [ ] All tasks completed
- [ ] All tests passing
- [ ] Staging deployment successful
- [ ] Metrics verified
- [ ] Production deployment approved
- [ ] Team trained
- [ ] Documentation updated

### Post-Implementation
- [ ] Monitor metrics for 1 week
- [ ] Collect user feedback
- [ ] Document lessons learned
- [ ] Plan next improvements

---

## 📞 Communication Plan

### Daily Standup
- 10:00 AM: 15-min sync
- Report: completed, in-progress, blockers
- Attendees: Backend, DevOps, Tech Lead

### Weekly Review
- Friday 4:00 PM: 30-min review
- Review: metrics, blockers, next week plan
- Attendees: All team members

### Stakeholder Updates
- Monday 9:00 AM: 15-min update
- Report: progress, metrics, risks
- Attendees: Product, Tech Lead, Stakeholders

---

## 🎓 Documentation

### For Developers
- `FANPAGE_IMPLEMENTATION_GUIDE.md` — Step-by-step implementation
- `FANPAGE_QUICK_REFERENCE.md` — Quick reference guide
- Code comments in implementation

### For DevOps
- `FANPAGE_ACTION_PLAN.md` — This document
- Deployment runbook
- Monitoring setup guide

### For Product
- `AUDIT_REPORT_FANPAGE_OPTIMIZATION.md` — Detailed analysis
- `FANPAGE_QUICK_REFERENCE.md` — Before/after comparison
- Success metrics dashboard

---

## 🔗 Related Documents

1. **AUDIT_REPORT_FANPAGE_OPTIMIZATION.md** — Detailed audit and analysis
2. **FANPAGE_IMPLEMENTATION_GUIDE.md** — Step-by-step implementation
3. **FANPAGE_QUICK_REFERENCE.md** — Quick reference and troubleshooting
4. **FANPAGE_TESTING_METRICS.md** — Testing strategy and metrics
5. **CLAUDE.md** — Project overview

---

## ✅ Sign-Off

- [ ] Backend Lead: _________________ Date: _______
- [ ] DevOps Lead: _________________ Date: _______
- [ ] Tech Lead: _________________ Date: _______
- [ ] Product Manager: _________________ Date: _______

---

**Status**: Ready for Implementation  
**Start Date**: 2026-05-15  
**Target Completion**: 2026-06-02  
**Expected ROI**: +40% user satisfaction, -60% latency

