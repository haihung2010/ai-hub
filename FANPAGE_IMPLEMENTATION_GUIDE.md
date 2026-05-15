# Fanpage Chatbot Implementation Guide
**Date**: 2026-05-15  
**Target**: Reduce latency p50 from 13.6s → 5s, improve quality +30%

---

## Quick Start: 3 Changes for Immediate Impact

### Change 1: Enable Failure Risk Scoring (5 min)
**File**: `.env`

```bash
# Before
ENABLE_FAILURE_RISK=false
FAILURE_RISK_LOG_ONLY=true

# After
ENABLE_FAILURE_RISK=true
FAILURE_RISK_LOG_ONLY=false
FAILURE_RISK_ENABLE_ACTIONS=true
FAILURE_RISK_HIGH_THRESHOLD=0.6
FAILURE_RISK_MEDIUM_THRESHOLD=0.3
```

**Effect**: Automatically triggers web search for uncertain answers, adds disclaimers

---

### Change 2: Parallel Memory + RAG Loading (30 min)
**File**: `app/services/ai_service.py`

**Current** (sequential):
```python
# Lines ~300-350 in chat_stream()
memory_block = self._build_memory_block(...)  # 0.5-1s
knowledge_block = self._build_knowledge_block(...)  # 1-2s
# Total: 1.5-3s
```

**New** (parallel):
```python
async def _load_context_parallel(
    self,
    user_id: str | None,
    tenant_id: str,
    project_id: str,
    query: str,
) -> tuple[list[str], str | None]:
    """Load memory + knowledge in parallel."""
    memory_task = asyncio.create_task(
        asyncio.to_thread(
            self._build_memory_block,
            user_id, tenant_id, project_id, query
        )
    )
    knowledge_task = asyncio.create_task(
        asyncio.to_thread(
            self._build_knowledge_block,
            tenant_id, project_id, query
        )
    )
    memory_blocks, knowledge_block = await asyncio.gather(
        memory_task, knowledge_task, return_exceptions=True
    )
    return memory_blocks or [], knowledge_block

# In chat_stream(), replace:
# memory_blocks = self._build_memory_block(...)
# knowledge_block = self._build_knowledge_block(...)
# With:
memory_blocks, knowledge_block = await self._load_context_parallel(
    user_id, tenant_id, project_id, req.user_message
)
```

**Effect**: -1-2s latency

---

### Change 3: Lazy Web Search (20 min)
**File**: `app/services/ai_service.py`

**Current** (always runs if pattern matches):
```python
# Lines ~250-280
if self._should_web_search(req.user_message):
    search_context, source_urls = self._build_search_context(
        self._extract_explicit_search_query(req.user_message) or req.user_message
    )
```

**New** (only if needed):
```python
async def _load_web_search_lazy(
    self,
    query: str,
    knowledge_block: str | None,
    explicit_search: bool,
) -> tuple[str | None, list[str]]:
    """Load web search only if needed."""
    # Always run if explicit /search: prefix
    if explicit_search:
        return self._build_search_context(query)
    
    # Only run if no knowledge results AND query has "?"
    if knowledge_block and "No results" not in knowledge_block:
        return None, []
    
    if "?" not in query:
        return None, []
    
    # Run web search in background, don't block
    try:
        return self._build_search_context(query)
    except Exception:
        return None, []

# In chat_stream():
explicit_search = self._explicit_search_query(req) is not None
search_context, source_urls = await self._load_web_search_lazy(
    req.user_message,
    knowledge_block,
    explicit_search,
)
```

**Effect**: -2-5s for non-search queries

---

## Detailed Implementation: Phase 1 (1-2 weeks)

### 1. Config Changes

**File**: `app/core/config.py`

Add after line 134 (after `project_context_sizes`):

```python
# Fanpage-specific optimizations
fanpage_max_history_messages: int = Field(
    default=10,
    ge=1,
    alias="FANPAGE_MAX_HISTORY_MESSAGES"
)
fanpage_knowledge_max_chunks: int = Field(
    default=6,
    ge=1,
    le=10,
    alias="FANPAGE_KNOWLEDGE_MAX_CHUNKS"
)
fanpage_knowledge_dedup_threshold: float = Field(
    default=0.9,
    ge=0.0,
    le=1.0,
    alias="FANPAGE_KNOWLEDGE_DEDUP_THRESHOLD"
)
fanpage_latency_threshold_ms: float = Field(
    default=3000.0,
    gt=0,
    alias="FANPAGE_LATENCY_THRESHOLD_MS"
)
fanpage_enable_fact_extraction: bool = Field(
    default=True,
    alias="FANPAGE_ENABLE_FACT_EXTRACTION"
)
fanpage_fact_extraction_threshold: int = Field(
    default=5,
    ge=1,
    alias="FANPAGE_FACT_EXTRACTION_THRESHOLD"
)
project_latency_thresholds: dict[str, float] = Field(
    default_factory=dict,
    alias="PROJECT_LATENCY_THRESHOLDS"
)
```

**Update `.env`**:
```bash
FANPAGE_MAX_HISTORY_MESSAGES=10
FANPAGE_KNOWLEDGE_MAX_CHUNKS=6
FANPAGE_KNOWLEDGE_DEDUP_THRESHOLD=0.9
FANPAGE_LATENCY_THRESHOLD_MS=3000
FANPAGE_ENABLE_FACT_EXTRACTION=true
FANPAGE_FACT_EXTRACTION_THRESHOLD=5
PROJECT_LATENCY_THRESHOLDS={"fanpage": 3000, "support": 5000}
```

---

### 2. AIService Optimizations

**File**: `app/services/ai_service.py`

#### 2.1 Add per-project history cap

Replace line ~225 (`_effective_history_cap`):

```python
@staticmethod
def _effective_history_cap(settings: Settings, model_mode: str, project_id: str = "") -> int:
    """Get effective history cap based on model mode and project."""
    if model_mode == "lite":
        if project_id == "fanpage":
            return settings.fanpage_max_history_messages
        return settings.lite_max_history_messages
    
    if project_id == "fanpage":
        return settings.fanpage_max_history_messages
    return settings.max_history_messages
```

Update call site (line ~450 in `chat_stream`):
```python
# Before
history_cap = self._effective_history_cap(self._settings, req.model_mode)

# After
history_cap = self._effective_history_cap(
    self._settings, req.model_mode, req.project_id
)
```

#### 2.2 Add per-project latency threshold

Add after line ~92 (in `__init__`):

```python
def _get_latency_threshold(self, project_id: str) -> float:
    """Get latency threshold for project."""
    if project_id in self._settings.project_latency_thresholds:
        return self._settings.project_latency_thresholds[project_id]
    if project_id == "fanpage":
        return self._settings.fanpage_latency_threshold_ms
    return self._settings.hybrid_latency_threshold_ms
```

Update latency tracker initialization (line ~89):
```python
# Before
self._latency_tracker = _LatencyTracker(
    window=settings.hybrid_latency_window,
    threshold_ms=settings.hybrid_latency_threshold_ms,
)

# After (keep as-is, but use _get_latency_threshold in routing logic)
self._latency_tracker = _LatencyTracker(
    window=settings.hybrid_latency_window,
    threshold_ms=settings.hybrid_latency_threshold_ms,
)
```

Update hybrid routing (line ~550, in `_select_provider`):
```python
# Before
if self._latency_tracker.is_elevated():
    return self._cloud

# After
threshold = self._get_latency_threshold(req.project_id)
if self._latency_tracker.is_elevated() and threshold < 8000:
    return self._cloud
```

#### 2.3 Parallel memory + RAG loading

Add new method after line ~300:

```python
async def _load_context_parallel(
    self,
    user_id: str | None,
    tenant_id: str,
    project_id: str,
    query: str,
) -> tuple[list[str], str | None]:
    """Load memory + knowledge in parallel."""
    async def load_memory():
        try:
            structmem = self._load_structmem(user_id, tenant_id, project_id, query)
            blocks = self._build_structmem_blocks(structmem)
            if blocks:
                return blocks
            
            summary = self._summaries.get_latest_summary(
                user_id, project_id, tenant_id
            ) if user_id and self._summaries else None
            if summary:
                return [f"### SYSTEM: CONVERSATION SUMMARY ###\n{summary}"]
            return []
        except Exception:
            logger.exception("Memory loading failed")
            return []
    
    async def load_knowledge():
        try:
            return self._build_knowledge_block(tenant_id, project_id, query)
        except Exception:
            logger.exception("Knowledge loading failed")
            return None
    
    memory_blocks, knowledge_block = await asyncio.gather(
        asyncio.to_thread(load_memory),
        asyncio.to_thread(load_knowledge),
        return_exceptions=False,
    )
    return memory_blocks or [], knowledge_block
```

Update `chat_stream()` (line ~450):
```python
# Before
memory_blocks = self._build_structmem_blocks(...)
knowledge_block = self._build_knowledge_block(...)

# After
memory_blocks, knowledge_block = await self._load_context_parallel(
    user_id, tenant_id, req.project_id, req.user_message
)
```

#### 2.4 Lazy web search

Add new method after line ~300:

```python
async def _load_web_search_lazy(
    self,
    query: str,
    knowledge_block: str | None,
    explicit_search: bool,
) -> tuple[str | None, list[str]]:
    """Load web search only if needed."""
    if not self._web_search or not self._settings.enable_web_search_tool:
        return None, []
    
    # Always run if explicit /search: prefix
    if explicit_search:
        return self._build_search_context(query)
    
    # Skip if we have good knowledge results
    if knowledge_block and "No results" not in knowledge_block:
        return None, []
    
    # Skip if query doesn't have question mark
    if "?" not in query:
        return None, []
    
    # Run web search
    try:
        return self._build_search_context(query)
    except Exception:
        logger.exception("Web search failed")
        return None, []
```

Update `chat_stream()` (line ~480):
```python
# Before
search_context, source_urls = self._build_search_context(...)

# After
explicit_search = self._explicit_search_query(req) is not None
search_context, source_urls = await self._load_web_search_lazy(
    req.user_message,
    knowledge_block,
    explicit_search,
)
```

#### 2.5 Skip reranker for high-confidence queries

Update `_build_knowledge_block()` (line ~165):

```python
def _build_knowledge_block(self, tenant_id: str, project_id: str, query: str) -> str | None:
    if not self._settings.enable_knowledge_rag or not self._knowledge_retrieval:
        return None
    
    results = self._knowledge_retrieval.search(
        tenant_id=tenant_id,
        project_id=project_id,
        query=query,
        limit=self._settings.knowledge_max_chunks,
    )
    
    if not results:
        return None
    
    # For fanpage: skip reranker if top result has high confidence
    if project_id == "fanpage" and results[0].score > 0.8:
        logger.info(
            "Skipping reranker for high-confidence query project=%s score=%.2f",
            project_id,
            results[0].score,
        )
        return self._knowledge_retrieval.format_for_prompt(results)
    
    logger.info(
        "Knowledge context injected tenant=%s project=%s chunks=%d",
        tenant_id,
        project_id,
        len(results),
    )
    return self._knowledge_retrieval.format_for_prompt(results)
```

---

### 3. Knowledge Retrieval Improvements

**File**: `app/services/knowledge_retrieval_service.py`

#### 3.1 Add deduplication

Add new method after line ~30:

```python
def _deduplicate_results(
    self,
    results: list[KnowledgeSearchResult],
    threshold: float = 0.9,
) -> list[KnowledgeSearchResult]:
    """Remove semantically similar results."""
    if not results or not self._embedding:
        return results
    
    unique = []
    for result in results:
        # Check if similar to any existing result
        is_duplicate = False
        for existing in unique:
            if not result.content or not existing.content:
                continue
            
            # Simple similarity check: token overlap
            result_tokens = self._tokenize(result.content)
            existing_tokens = self._tokenize(existing.content)
            
            if result_tokens and existing_tokens:
                overlap = len(result_tokens & existing_tokens) / len(result_tokens | existing_tokens)
                if overlap > threshold:
                    is_duplicate = True
                    break
        
        if not is_duplicate:
            unique.append(result)
    
    return unique
```

Update `search()` method (line ~37):

```python
def search(
    self,
    *,
    tenant_id: str,
    project_id: str,
    query: str,
    limit: int = 4,
    knowledge_domain: str | None = None,
) -> list[KnowledgeSearchResult]:
    query_tokens = self._tokenize(query)
    if not query_tokens:
        return []

    query_embedding = self._embedding.embed(query) if self._embedding else None

    # ... existing SQL code ...

    scored = [self._score_row(row, query_tokens, query_embedding) for row in rows]
    relevant = [item for item in scored if item.score > 0]
    relevant.sort(key=lambda item: item.score, reverse=True)

    # Deduplicate before reranking
    dedup_threshold = 0.9  # Could be configurable
    relevant = [
        _ScoredChunk(r.result, r.score)
        for r in self._deduplicate_results(
            [item.result for item in relevant],
            threshold=dedup_threshold,
        )
    ]

    if self._rerank and relevant:
        candidates = relevant[:_RERANK_CANDIDATE_K]
        docs = [c.result.content for c in candidates]
        reranked = self._rerank.rerank(query, docs)
        return [candidates[r.index].result for r in reranked[:limit]]

    return [item.result for item in relevant[:limit]]
```

---

### 4. Fanpage-Specific Prompt

**File**: `app/prompts/fanpage.md`

Create new file:

```markdown
---
model: local-gemma4-e4b-q8
provider: local
temperature: 0.7
enable_search: true
---

You are a helpful customer support assistant for our fanpage.

**Your Role**: Answer product questions, resolve customer issues, and engage with customers in a friendly and professional manner.

**Tone & Style**:
- Friendly and approachable, but professional
- Concise (aim for 2-3 sentences per response)
- Empathetic and understanding
- Use simple, clear language

**Guidelines**:
1. **Use Knowledge Base**: Always check the knowledge base for product information, policies, and FAQs before answering.
2. **Be Honest**: If you don't know something, say "I'll check with our team and get back to you" instead of guessing.
3. **Suggest Related Products**: When relevant, mention related products or services the customer might be interested in.
4. **Handle Complaints**: Listen empathetically, acknowledge the issue, and offer a solution or escalation path.
5. **Avoid Hallucination**: Never make up product features, prices, or policies. Always refer to the knowledge base.

**Common Scenarios**:
- Product Questions: Use knowledge base to provide accurate details
- Pricing/Promotions: Check current promotions in knowledge base
- Shipping/Returns: Refer to shipping and return policy
- Technical Issues: Provide troubleshooting steps or escalate to support team
- Complaints: Apologize, understand the issue, offer solution

**Current Date**: Use today's date when relevant (e.g., "Our current promotion ends on...")

Remember: Your goal is to provide helpful, accurate information and create a positive customer experience.
```

---

### 5. New Service: Fact Extraction

**File**: `app/services/fact_extraction_service.py`

Create new file:

```python
"""Lightweight fact extraction for fanpage conversations."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence

from app.core.database import DEFAULT_TENANT_ID, get_db_connection
from app.models.chat import Message
from app.services.pinned_memory_service import PinnedMemoryService

logger = logging.getLogger(__name__)

FACT_EXTRACTION_PROMPT = """Extract 3-5 key facts from this conversation.
Return strict JSON with key "facts" mapping to a list of strings.
Each fact should be a single, clear statement (e.g., "User name is John", "Interested in Product X").
Only extract durable facts that will be useful in future conversations.
Ignore chit-chat and temporary context.

Example:
{"facts": ["User name is John", "Interested in Product X", "Budget is $500", "Prefers email communication"]}
"""


class FactExtractionService:
    def __init__(self, pinned_memory: PinnedMemoryService | None = None) -> None:
        self._pinned_memory = pinned_memory

    def _build_prompt(self, messages: Sequence[tuple[int, Message]]) -> list[Message]:
        source_text = "\n".join(
            f"{message.role}: {message.content}" for _, message in messages
        )
        return [
            Message(role="system", content=FACT_EXTRACTION_PROMPT),
            Message(role="user", content=source_text),
        ]

    def _parse_payload(self, payload: str) -> list[str]:
        try:
            parsed = json.loads(payload)
            facts = parsed.get("facts", [])
            return [str(f).strip() for f in facts if f]
        except (json.JSONDecodeError, TypeError):
            logger.warning("Fact extraction returned invalid JSON")
            return []

    async def extract_and_store(
        self,
        *,
        user_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        project_id: str,
        session_id: str,
        messages: Sequence[tuple[int, Message]],
        provider,
        model: str,
    ) -> list[str]:
        """Extract facts and store in pinned memory."""
        if not messages or not self._pinned_memory:
            return []

        prompt_messages = self._build_prompt(messages)
        try:
            payload = await provider.complete(prompt_messages, model, 0.2)
            facts = self._parse_payload(payload)
        except Exception:
            logger.exception("Fact extraction failed user=%s project=%s", user_id, project_id)
            return []

        # Store facts in pinned memory
        stored_facts = []
        for fact in facts:
            try:
                # Use fact as both key and value for simplicity
                key = fact[:50].lower().replace(" ", "_")
                self._pinned_memory.upsert_memory(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    user_id=user_id,
                    key=key,
                    value=fact,
                    scope="user",
                    confidence=0.8,
                    source_session_id=session_id,
                )
                stored_facts.append(fact)
            except Exception:
                logger.exception("Failed to store fact: %s", fact)

        logger.info(
            "Extracted and stored facts user=%s project=%s count=%d",
            user_id,
            project_id,
            len(stored_facts),
        )
        return stored_facts
```

---

### 6. Update Main App Initialization

**File**: `app/main.py`

Add fact extraction service initialization (around line ~80):

```python
from app.services.fact_extraction_service import FactExtractionService

# In app_factory():
fact_extraction = (
    FactExtractionService(pinned_memory=pinned_memory)
    if settings.fanpage_enable_fact_extraction
    else None
)

ai_service = AIService(
    local=local_provider,
    history=history_service,
    settings=settings,
    users=user_service,
    summaries=summary_service,
    web_search=web_search_service,
    memory_retrieval=memory_retrieval_service,
    structmem=structmem_service,
    predictions=prediction_service,
    pinned_memory=pinned_memory,
    cloud=cloud_provider,
    usage=usage_service,
    failure_risk=failure_risk_service,
    knowledge_retrieval=knowledge_retrieval_service,
    background_local=background_provider,
    fact_extraction=fact_extraction,  # Add this
)
```

Update AIService constructor (line ~55):

```python
def __init__(
    self,
    local: ChatProvider,
    history: HistoryService,
    settings: Settings,
    users: UserService,
    summaries: SummaryService | None = None,
    web_search: WebSearchService | None = None,
    memory_retrieval: MemoryRetrievalService | None = None,
    structmem: StructMemService | None = None,
    predictions: PredictionService | None = None,
    pinned_memory: PinnedMemoryService | None = None,
    cloud: ChatProvider | None = None,
    usage: UsageService | None = None,
    failure_risk: FailureRiskService | None = None,
    knowledge_retrieval: KnowledgeRetrievalService | None = None,
    background_local: ChatProvider | None = None,
    fact_extraction: FactExtractionService | None = None,  # Add this
) -> None:
    # ... existing code ...
    self._fact_extraction = fact_extraction
```

---

### 7. Schedule Fact Extraction

**File**: `app/services/ai_service.py`

Update `_schedule_memory_jobs()` (line ~184):

```python
def _schedule_memory_jobs(
    self,
    user_id: str | None,
    tenant_id: str,
    project_id: str,
    session_id: str,
    provider: ChatProvider,
) -> None:
    """Schedule async memory extraction jobs."""
    bg_provider = self._background_local or provider

    # Mutually exclusive: StructMem and SummaryService
    if self._settings.enable_structmem and user_id and self._structmem:
        asyncio.create_task(
            self._structmem.process_recent_messages(
                user_id=user_id,
                tenant_id=tenant_id,
                project_id=project_id,
                session_id=session_id,
                provider=bg_provider,
                model=self._settings.structmem_extraction_model,
                threshold=self._settings.structmem_extraction_threshold,
                consolidation_model=self._settings.structmem_consolidation_model,
            )
        )
        return
    
    if user_id and self._summaries:
        asyncio.create_task(
            self._summaries.summarize(
                user_id,
                project_id,
                bg_provider,
                self._settings.summary_model,
                self._settings.summary_threshold,
                tenant_id,
                self._settings.summary_context_token_threshold,
            )
        )
    
    # NEW: Schedule fact extraction for fanpage
    if (
        project_id == "fanpage"
        and user_id
        and self._fact_extraction
        and self._settings.fanpage_enable_fact_extraction
    ):
        unsummarized = self._history.get_unsummarized_messages(
            user_id, project_id, tenant_id
        )
        if len(unsummarized) >= self._settings.fanpage_fact_extraction_threshold:
            asyncio.create_task(
                self._fact_extraction.extract_and_store(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    session_id=session_id,
                    messages=unsummarized,
                    provider=bg_provider,
                    model=self._settings.summary_model,
                )
            )
```

---

## Testing Checklist

### Unit Tests
- [ ] `test_fact_extraction_service.py` — Fact extraction accuracy
- [ ] `test_parallel_loading.py` — Parallel memory + RAG loading
- [ ] `test_lazy_web_search.py` — Web search triggering logic

### Integration Tests
- [ ] `test_fanpage_latency.py` — p50 < 5s, p95 < 8s
- [ ] `test_fanpage_quality.py` — Hallucination rate < 20%
- [ ] `test_fanpage_memory.py` — Fact extraction success > 90%

### Load Tests
- [ ] 100 concurrent fanpage requests
- [ ] Assert no OOM, no crashes
- [ ] Assert p50 latency < 5s

---

## Deployment Checklist

- [ ] Update `.env` with new settings
- [ ] Update `app/core/config.py` with new fields
- [ ] Update `app/services/ai_service.py` with optimizations
- [ ] Create `app/services/fact_extraction_service.py`
- [ ] Create `app/prompts/fanpage.md`
- [ ] Update `app/main.py` to initialize fact extraction
- [ ] Run tests: `pytest tests/ -v`
- [ ] Deploy to staging
- [ ] Monitor metrics (latency, quality, errors)
- [ ] Gradual rollout: 10% → 50% → 100%
- [ ] Deploy to production

---

## Monitoring & Alerts

### Key Metrics to Track
```python
# In admin dashboard
fanpage_p50_latency
fanpage_p95_latency
fanpage_error_rate
fanpage_hallucination_rate
fanpage_fact_extraction_success_rate
fanpage_avg_response_quality
```

### Alert Thresholds
```
Alert if fanpage_p50_latency > 5s
Alert if fanpage_error_rate > 5%
Alert if fanpage_hallucination_rate > 20%
Alert if fanpage_fact_extraction_success_rate < 90%
```

---

## Rollback Plan

If any metric degrades:
1. Disable fact extraction: `FANPAGE_ENABLE_FACT_EXTRACTION=false`
2. Revert parallel loading: Use sequential loading
3. Revert lazy web search: Always run web search
4. Revert to original history cap: `FANPAGE_MAX_HISTORY_MESSAGES=20`

---

## Success Criteria

✅ **Latency**: p50 < 5s (from 13.6s)  
✅ **Quality**: Hallucination rate < 20% (from ~30%)  
✅ **Memory**: Fact extraction success > 90%  
✅ **Reliability**: Error rate < 5%  
✅ **User Satisfaction**: > 4.0/5.0

