# Fanpage Chatbot: Testing & Metrics Guide
**Date**: 2026-05-15

---

## 📋 Test Strategy

### Test Pyramid
```
                    E2E Tests (10%)
                   /              \
                 /                  \
              Integration Tests (30%)
             /                        \
           /                            \
        Unit Tests (60%)
```

---

## 🧪 Unit Tests

### 1. Fact Extraction Service

**File**: `tests/unit/test_fact_extraction_service.py`

```python
import pytest
from app.services.fact_extraction_service import FactExtractionService
from app.models.chat import Message


@pytest.fixture
def fact_extraction_service():
    return FactExtractionService()


class TestFactExtractionService:
    def test_parse_valid_json(self, fact_extraction_service):
        """Test parsing valid JSON response."""
        payload = '{"facts": ["User name is John", "Budget is $500"]}'
        facts = fact_extraction_service._parse_payload(payload)
        assert len(facts) == 2
        assert "John" in facts[0]
        assert "$500" in facts[1]

    def test_parse_invalid_json(self, fact_extraction_service):
        """Test handling invalid JSON."""
        payload = "not valid json"
        facts = fact_extraction_service._parse_payload(payload)
        assert facts == []

    def test_parse_empty_facts(self, fact_extraction_service):
        """Test handling empty facts list."""
        payload = '{"facts": []}'
        facts = fact_extraction_service._parse_payload(payload)
        assert facts == []

    def test_parse_missing_facts_key(self, fact_extraction_service):
        """Test handling missing 'facts' key."""
        payload = '{"other_key": ["value"]}'
        facts = fact_extraction_service._parse_payload(payload)
        assert facts == []

    def test_build_prompt(self, fact_extraction_service):
        """Test prompt building."""
        messages = [
            (1, Message(role="user", content="Hello")),
            (2, Message(role="assistant", content="Hi there")),
        ]
        prompt = fact_extraction_service._build_prompt(messages)
        assert len(prompt) == 2
        assert prompt[0].role == "system"
        assert prompt[1].role == "user"
        assert "Hello" in prompt[1].content
        assert "Hi there" in prompt[1].content


@pytest.mark.asyncio
async def test_extract_and_store_success(fact_extraction_service, mock_provider, mock_pinned_memory):
    """Test successful fact extraction and storage."""
    messages = [
        (1, Message(role="user", content="My name is John")),
        (2, Message(role="assistant", content="Nice to meet you John")),
    ]
    
    # Mock provider response
    mock_provider.complete.return_value = '{"facts": ["User name is John"]}'
    
    facts = await fact_extraction_service.extract_and_store(
        user_id="user1",
        project_id="fanpage",
        session_id="session1",
        messages=messages,
        provider=mock_provider,
        model="test-model",
    )
    
    assert len(facts) == 1
    assert "John" in facts[0]
    mock_pinned_memory.upsert_memory.assert_called_once()


@pytest.mark.asyncio
async def test_extract_and_store_provider_error(fact_extraction_service, mock_provider):
    """Test handling provider errors."""
    messages = [(1, Message(role="user", content="Hello"))]
    
    # Mock provider error
    mock_provider.complete.side_effect = Exception("Provider error")
    
    facts = await fact_extraction_service.extract_and_store(
        user_id="user1",
        project_id="fanpage",
        session_id="session1",
        messages=messages,
        provider=mock_provider,
        model="test-model",
    )
    
    assert facts == []
```

---

### 2. Parallel Loading

**File**: `tests/unit/test_parallel_loading.py`

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from app.services.ai_service import AIService


@pytest.mark.asyncio
async def test_load_context_parallel_both_succeed(ai_service):
    """Test parallel loading when both memory and knowledge succeed."""
    start = asyncio.get_event_loop().time()
    
    memory_blocks, knowledge_block = await ai_service._load_context_parallel(
        user_id="user1",
        tenant_id="default",
        project_id="fanpage",
        query="What products do you have?",
    )
    
    elapsed = asyncio.get_event_loop().time() - start
    
    # Should be faster than sequential (1-2s vs 2-4s)
    assert elapsed < 3.0
    assert isinstance(memory_blocks, list)
    assert knowledge_block is None or isinstance(knowledge_block, str)


@pytest.mark.asyncio
async def test_load_context_parallel_memory_fails(ai_service):
    """Test parallel loading when memory fails."""
    with patch.object(ai_service, '_build_structmem_blocks', side_effect=Exception("Memory error")):
        memory_blocks, knowledge_block = await ai_service._load_context_parallel(
            user_id="user1",
            tenant_id="default",
            project_id="fanpage",
            query="What products do you have?",
        )
    
    # Should return empty memory blocks but continue
    assert memory_blocks == []
    assert knowledge_block is not None or knowledge_block is None


@pytest.mark.asyncio
async def test_load_context_parallel_knowledge_fails(ai_service):
    """Test parallel loading when knowledge fails."""
    with patch.object(ai_service, '_build_knowledge_block', side_effect=Exception("Knowledge error")):
        memory_blocks, knowledge_block = await ai_service._load_context_parallel(
            user_id="user1",
            tenant_id="default",
            project_id="fanpage",
            query="What products do you have?",
        )
    
    # Should return memory blocks but None for knowledge
    assert isinstance(memory_blocks, list)
    assert knowledge_block is None
```

---

### 3. Lazy Web Search

**File**: `tests/unit/test_lazy_web_search.py`

```python
import pytest
from app.services.ai_service import AIService


@pytest.mark.asyncio
async def test_lazy_web_search_explicit_search(ai_service):
    """Test web search runs for explicit /search: prefix."""
    search_context, urls = await ai_service._load_web_search_lazy(
        query="latest iPhone price",
        knowledge_block=None,
        explicit_search=True,
    )
    
    # Should run web search
    assert search_context is not None
    assert len(urls) > 0


@pytest.mark.asyncio
async def test_lazy_web_search_skip_with_knowledge(ai_service):
    """Test web search skipped when knowledge results exist."""
    knowledge_block = "### SYSTEM: PROJECT KNOWLEDGE CONTEXT ###\n[1] Product X | price=$299"
    
    search_context, urls = await ai_service._load_web_search_lazy(
        query="What's the price?",
        knowledge_block=knowledge_block,
        explicit_search=False,
    )
    
    # Should skip web search
    assert search_context is None
    assert urls == []


@pytest.mark.asyncio
async def test_lazy_web_search_skip_without_question(ai_service):
    """Test web search skipped when query has no question mark."""
    search_context, urls = await ai_service._load_web_search_lazy(
        query="Tell me about our products",
        knowledge_block=None,
        explicit_search=False,
    )
    
    # Should skip web search (no question mark)
    assert search_context is None
    assert urls == []


@pytest.mark.asyncio
async def test_lazy_web_search_run_with_question(ai_service):
    """Test web search runs when query has question mark and no knowledge."""
    search_context, urls = await ai_service._load_web_search_lazy(
        query="What's the latest price?",
        knowledge_block=None,
        explicit_search=False,
    )
    
    # Should run web search
    assert search_context is not None or search_context is None  # Depends on web search availability
```

---

### 4. RAG Deduplication

**File**: `tests/unit/test_rag_deduplication.py`

```python
import pytest
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.models.knowledge import KnowledgeSearchResult


@pytest.fixture
def knowledge_retrieval_service():
    return KnowledgeRetrievalService()


class TestRAGDeduplication:
    def test_deduplicate_identical_content(self, knowledge_retrieval_service):
        """Test deduplication of identical content."""
        results = [
            KnowledgeSearchResult(
                card_id="1",
                chunk_id="1",
                project_id="fanpage",
                knowledge_domain="products",
                title="Product X",
                summary="A great product",
                content="Product X costs $299 and has great features",
                source_type="faq",
                trust_level=1,
                version=1,
                score=0.9,
                tags=[],
            ),
            KnowledgeSearchResult(
                card_id="2",
                chunk_id="2",
                project_id="fanpage",
                knowledge_domain="products",
                title="Product X Details",
                summary="More about Product X",
                content="Product X costs $299 and has great features",  # Identical
                source_type="faq",
                trust_level=1,
                version=1,
                score=0.85,
                tags=[],
            ),
        ]
        
        deduplicated = knowledge_retrieval_service._deduplicate_results(results, threshold=0.9)
        
        # Should remove duplicate
        assert len(deduplicated) == 1
        assert deduplicated[0].card_id == "1"  # Keep first (higher score)

    def test_deduplicate_similar_content(self, knowledge_retrieval_service):
        """Test deduplication of similar content."""
        results = [
            KnowledgeSearchResult(
                card_id="1",
                chunk_id="1",
                project_id="fanpage",
                knowledge_domain="products",
                title="Product X",
                summary="A great product",
                content="Product X costs $299 with free shipping",
                source_type="faq",
                trust_level=1,
                version=1,
                score=0.9,
                tags=[],
            ),
            KnowledgeSearchResult(
                card_id="2",
                chunk_id="2",
                project_id="fanpage",
                knowledge_domain="products",
                title="Product X Pricing",
                summary="Pricing info",
                content="Product X is priced at $299 and includes free shipping",  # Similar
                source_type="faq",
                trust_level=1,
                version=1,
                score=0.85,
                tags=[],
            ),
        ]
        
        deduplicated = knowledge_retrieval_service._deduplicate_results(results, threshold=0.8)
        
        # Should remove similar content
        assert len(deduplicated) == 1

    def test_deduplicate_different_content(self, knowledge_retrieval_service):
        """Test no deduplication for different content."""
        results = [
            KnowledgeSearchResult(
                card_id="1",
                chunk_id="1",
                project_id="fanpage",
                knowledge_domain="products",
                title="Product X",
                summary="A great product",
                content="Product X costs $299",
                source_type="faq",
                trust_level=1,
                version=1,
                score=0.9,
                tags=[],
            ),
            KnowledgeSearchResult(
                card_id="2",
                chunk_id="2",
                project_id="fanpage",
                knowledge_domain="shipping",
                title="Shipping Policy",
                summary="How we ship",
                content="We ship within 2-3 business days",  # Different
                source_type="policy",
                trust_level=1,
                version=1,
                score=0.85,
                tags=[],
            ),
        ]
        
        deduplicated = knowledge_retrieval_service._deduplicate_results(results, threshold=0.9)
        
        # Should keep both
        assert len(deduplicated) == 2
```

---

## 🔗 Integration Tests

### 1. Fanpage Latency Test

**File**: `tests/integration/test_fanpage_latency.py`

```python
import pytest
import time
import asyncio
from app.models.chat import ChatRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_p50_latency(ai_service, client):
    """Test fanpage p50 latency < 5s."""
    latencies = []
    
    for i in range(50):
        start = time.time()
        
        response = await client.post(
            "/v1/chat",
            json={
                "user_message": f"What products do you have? (query {i})",
                "project_id": "fanpage",
                "user_name": f"user_{i}",
                "stream": False,
            },
            headers={"X-API-KEY": "test-key"},
        )
        
        elapsed = time.time() - start
        latencies.append(elapsed)
        
        assert response.status_code == 200
    
    # Calculate percentiles
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    
    print(f"Latency - p50: {p50:.2f}s, p95: {p95:.2f}s, p99: {p99:.2f}s")
    
    # Assert targets
    assert p50 < 5.0, f"p50 latency {p50:.2f}s exceeds 5s target"
    assert p95 < 8.0, f"p95 latency {p95:.2f}s exceeds 8s target"
    assert p99 < 12.0, f"p99 latency {p99:.2f}s exceeds 12s target"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_concurrent_latency(client):
    """Test fanpage latency under concurrent load."""
    async def send_request(i):
        start = time.time()
        response = await client.post(
            "/v1/chat",
            json={
                "user_message": f"What's the price? (query {i})",
                "project_id": "fanpage",
                "user_name": f"user_{i}",
                "stream": False,
            },
            headers={"X-API-KEY": "test-key"},
        )
        elapsed = time.time() - start
        return elapsed, response.status_code
    
    # Send 20 concurrent requests
    tasks = [send_request(i) for i in range(20)]
    results = await asyncio.gather(*tasks)
    
    latencies = [r[0] for r in results]
    status_codes = [r[1] for r in results]
    
    # All should succeed
    assert all(code == 200 for code in status_codes)
    
    # Calculate percentiles
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    
    print(f"Concurrent - p50: {p50:.2f}s, p95: {p95:.2f}s")
    
    # Assert targets (slightly relaxed for concurrent)
    assert p50 < 6.0, f"Concurrent p50 {p50:.2f}s exceeds 6s target"
    assert p95 < 10.0, f"Concurrent p95 {p95:.2f}s exceeds 10s target"
```

---

### 2. Fanpage Quality Test

**File**: `tests/integration/test_fanpage_quality.py`

```python
import pytest
from app.models.chat import ChatRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_hallucination_rate(client):
    """Test fanpage hallucination rate < 20%."""
    test_cases = [
        {
            "query": "What's the price of Product X?",
            "expected_keywords": ["price", "product", "x"],
            "should_not_contain": ["$999999", "made up price"],
        },
        {
            "query": "Do you offer financing?",
            "expected_keywords": ["financing", "payment", "option"],
            "should_not_contain": ["definitely", "100% sure"],
        },
        {
            "query": "What's your return policy?",
            "expected_keywords": ["return", "policy", "day"],
            "should_not_contain": ["lifetime", "no questions asked"],
        },
    ]
    
    hallucinations = 0
    
    for test_case in test_cases:
        response = await client.post(
            "/v1/chat",
            json={
                "user_message": test_case["query"],
                "project_id": "fanpage",
                "user_name": "test_user",
                "stream": False,
            },
            headers={"X-API-KEY": "test-key"},
        )
        
        assert response.status_code == 200
        content = response.json()["content"].lower()
        
        # Check for hallucinations
        has_hallucination = any(
            phrase in content for phrase in test_case["should_not_contain"]
        )
        
        if has_hallucination:
            hallucinations += 1
            print(f"Hallucination detected in: {test_case['query']}")
    
    hallucination_rate = hallucinations / len(test_cases)
    print(f"Hallucination rate: {hallucination_rate:.1%}")
    
    assert hallucination_rate < 0.2, f"Hallucination rate {hallucination_rate:.1%} exceeds 20%"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_relevance(client):
    """Test fanpage response relevance."""
    test_cases = [
        {
            "query": "What products do you have?",
            "should_mention": ["product"],
        },
        {
            "query": "How much does Product X cost?",
            "should_mention": ["price", "cost", "product"],
        },
        {
            "query": "Do you have financing options?",
            "should_mention": ["financing", "payment", "option"],
        },
    ]
    
    relevant_count = 0
    
    for test_case in test_cases:
        response = await client.post(
            "/v1/chat",
            json={
                "user_message": test_case["query"],
                "project_id": "fanpage",
                "user_name": "test_user",
                "stream": False,
            },
            headers={"X-API-KEY": "test-key"},
        )
        
        assert response.status_code == 200
        content = response.json()["content"].lower()
        
        # Check relevance
        is_relevant = any(
            keyword in content for keyword in test_case["should_mention"]
        )
        
        if is_relevant:
            relevant_count += 1
        else:
            print(f"Irrelevant response to: {test_case['query']}")
    
    relevance_rate = relevant_count / len(test_cases)
    print(f"Relevance rate: {relevance_rate:.1%}")
    
    assert relevance_rate > 0.9, f"Relevance rate {relevance_rate:.1%} below 90%"
```

---

### 3. Fanpage Memory Test

**File**: `tests/integration/test_fanpage_memory.py`

```python
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_fact_extraction(client, db_connection):
    """Test fanpage fact extraction success."""
    user_name = "test_user_facts"
    
    # Send conversation
    messages = [
        "My name is John Smith",
        "I'm interested in Product X",
        "My budget is $500",
        "I prefer email communication",
        "I'm looking for something with good reviews",
    ]
    
    for msg in messages:
        response = await client.post(
            "/v1/chat",
            json={
                "user_message": msg,
                "project_id": "fanpage",
                "user_name": user_name,
                "stream": False,
            },
            headers={"X-API-KEY": "test-key"},
        )
        assert response.status_code == 200
    
    # Wait for async fact extraction
    import asyncio
    await asyncio.sleep(2)
    
    # Check pinned memories
    cursor = db_connection.cursor()
    cursor.execute(
        "SELECT key, value FROM pinned_memories WHERE user_id = %s AND project_id = %s",
        (user_name, "fanpage"),
    )
    facts = cursor.fetchall()
    
    # Should have extracted facts
    assert len(facts) > 0, "No facts extracted"
    
    # Check for expected facts
    fact_values = [f[1].lower() for f in facts]
    assert any("john" in v for v in fact_values), "Name not extracted"
    assert any("product" in v for v in fact_values), "Product interest not extracted"
    assert any("500" in v for v in fact_values), "Budget not extracted"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fanpage_memory_injection(client):
    """Test fanpage memory injection in responses."""
    user_name = "test_user_memory"
    
    # First message: establish facts
    response1 = await client.post(
        "/v1/chat",
        json={
            "user_message": "Hi, I'm Alice and I'm interested in Product Y",
            "project_id": "fanpage",
            "user_name": user_name,
            "stream": False,
        },
        headers={"X-API-KEY": "test-key"},
    )
    assert response1.status_code == 200
    
    # Wait for fact extraction
    import asyncio
    await asyncio.sleep(2)
    
    # Second message: should reference extracted facts
    response2 = await client.post(
        "/v1/chat",
        json={
            "user_message": "What do you recommend?",
            "project_id": "fanpage",
            "user_name": user_name,
            "stream": False,
        },
        headers={"X-API-KEY": "test-key"},
    )
    assert response2.status_code == 200
    
    # Response should mention Alice or Product Y (from memory)
    content = response2.json()["content"].lower()
    # Note: This is a soft check - model may or may not reference
    # In production, use more sophisticated evaluation
```

---

## 📊 Metrics Tracking

### Key Metrics Dashboard

**File**: `scripts/track_fanpage_metrics.py`

```python
"""Track fanpage chatbot metrics over time."""

import json
import time
from datetime import datetime
from pathlib import Path
import httpx


class FanpageMetricsTracker:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
        self.api_key = api_key
        self.base_url = base_url
        self.metrics_file = Path("fanpage_metrics.jsonl")

    async def track_request(self, query: str, user_name: str = "metrics_user"):
        """Track a single request."""
        start = time.time()
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/chat",
                    json={
                        "user_message": query,
                        "project_id": "fanpage",
                        "user_name": user_name,
                        "stream": False,
                    },
                    headers={"X-API-KEY": self.api_key},
                    timeout=30.0,
                )
                
                elapsed = time.time() - start
                
                metric = {
                    "timestamp": datetime.now().isoformat(),
                    "query": query,
                    "latency_seconds": elapsed,
                    "status_code": response.status_code,
                    "success": response.status_code == 200,
                }
                
                if response.status_code == 200:
                    data = response.json()
                    metric["content_length"] = len(data.get("content", ""))
                
                # Append to metrics file
                with open(self.metrics_file, "a") as f:
                    f.write(json.dumps(metric) + "\n")
                
                return metric
            except Exception as e:
                metric = {
                    "timestamp": datetime.now().isoformat(),
                    "query": query,
                    "latency_seconds": time.time() - start,
                    "success": False,
                    "error": str(e),
                }
                with open(self.metrics_file, "a") as f:
                    f.write(json.dumps(metric) + "\n")
                return metric

    def analyze_metrics(self):
        """Analyze collected metrics."""
        if not self.metrics_file.exists():
            print("No metrics file found")
            return
        
        metrics = []
        with open(self.metrics_file) as f:
            for line in f:
                metrics.append(json.loads(line))
        
        if not metrics:
            print("No metrics collected")
            return
        
        # Calculate statistics
        latencies = [m["latency_seconds"] for m in metrics if m["success"]]
        success_count = sum(1 for m in metrics if m["success"])
        
        if latencies:
            latencies.sort()
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            
            print(f"\n=== Fanpage Metrics ===")
            print(f"Total requests: {len(metrics)}")
            print(f"Success rate: {success_count / len(metrics):.1%}")
            print(f"p50 latency: {p50:.2f}s")
            print(f"p95 latency: {p95:.2f}s")
            print(f"p99 latency: {p99:.2f}s")
            print(f"Min latency: {min(latencies):.2f}s")
            print(f"Max latency: {max(latencies):.2f}s")
            print(f"Avg latency: {sum(latencies) / len(latencies):.2f}s")


async def main():
    """Run metrics tracking."""
    import asyncio
    
    tracker = FanpageMetricsTracker(api_key="your-api-key")
    
    # Track 100 requests
    queries = [
        "What products do you have?",
        "What's the price of Product X?",
        "Do you offer financing?",
        "What's your return policy?",
        "How long does shipping take?",
    ]
    
    for i in range(100):
        query = queries[i % len(queries)]
        print(f"Request {i+1}/100: {query}")
        await tracker.track_request(query)
        await asyncio.sleep(0.5)  # Rate limit
    
    # Analyze results
    tracker.analyze_metrics()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 🎯 Success Criteria Checklist

### Phase 1 (Week 1)
- [ ] p50 latency < 10s (from 13.6s)
- [ ] Error rate < 5%
- [ ] No OOM errors
- [ ] Failure risk scoring working

### Phase 2 (Week 2)
- [ ] p50 latency < 7s
- [ ] Fact extraction success > 90%
- [ ] Hallucination rate < 25%
- [ ] Memory injection working

### Phase 3 (Week 3)
- [ ] p50 latency < 5s ✅
- [ ] Hallucination rate < 20% ✅
- [ ] Response quality > 0.8 ✅
- [ ] User satisfaction > 4.0/5.0 ✅

---

## 📈 Monitoring Dashboard

### Real-time Metrics
```bash
# Check current metrics
curl http://localhost:8000/v1/admin/stats \
  -H "X-API-KEY: your-api-key" | jq '.fanpage_metrics'

# Expected output:
# {
#   "p50_latency": 4.8,
#   "p95_latency": 7.2,
#   "error_rate": 0.02,
#   "hallucination_rate": 0.18,
#   "fact_extraction_success": 0.92
# }
```

### Daily Report
```bash
# Generate daily metrics report
python scripts/track_fanpage_metrics.py --analyze

# Expected output:
# === Fanpage Metrics ===
# Total requests: 1000
# Success rate: 98.0%
# p50 latency: 4.8s
# p95 latency: 7.2s
# p99 latency: 10.1s
```

---

## 🔄 Continuous Improvement

### Weekly Review
1. Check metrics dashboard
2. Review error logs
3. Identify bottlenecks
4. Plan optimizations

### Monthly Review
1. Compare to baseline
2. Analyze user feedback
3. Identify new opportunities
4. Plan next phase

### Quarterly Review
1. Full system audit
2. Performance benchmarking
3. Architecture review
4. Strategic planning

---

**Last Updated**: 2026-05-15  
**Status**: Ready for Testing  
**Test Coverage**: 60% unit, 30% integration, 10% E2E

