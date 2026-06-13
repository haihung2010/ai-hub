# E-commerce 100-User Test Design

**Date:** 2026-06-13
**Status:** Approved
**Author:** Brainstorming session with user
**Related:**
- `docs/superpowers/specs/2026-06-12-ai-hub-comprehensive-test-design.md` (predecessor test)
- `app/services/structmem_service.py`, `app/services/verbatim_memory.py` (existing memory infra)
- `app/core/database.py` (schema)

---

## 1. Background & Motivation

User scenario: Một shop bán hàng dùng ai-hub, 100 khách/tuần, mỗi khách 5-10 câu hỏi tư vấn trước khi mua. Sau đó 1+ ngày, khách quay lại yêu cầu đổi trả (cần mã đơn + sản phẩm lỗi). Lần mua tiếp theo cần memory để personalize.

**Hiện trạng ai-hub:**
- Memory per-session (structmem, summary, verbatim theo session_id)
- Có VerbatimMemory query by user_id (cross-session OK)
- KHÔNG có `orders` table
- KHÔNG có cross-session profile aggregation

**Mục tiêu:** Test realistic e-commerce flow với 100 users × 3 sessions, verify 4 success criteria:
1. Return/warranty: AI lookup được order bằng mã
2. Cross-session memory: 1+ day sau AI vẫn nhớ preferences
3. Personalization: future purchase dùng history
4. Multi-tenant: 100 users, không leak data

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Test flow per user (3 sessions over 5 days compressed to 25m) │
│                                                                 │
│  Day 1 (compressed to 5 min) — Pre-purchase Q&A                │
│    ├─ User sends 7 questions (size, price, color, perf)         │
│    ├─ ai-hub responds + stores in structmem                     │
│    └─ Q7: "Đặt mua 1 cái" → POST /v1/orders (mock)           │
│                                                                 │
│  Day 2-3 (compressed to 10 min) — Return / Warranty             │
│    ├─ User comes back: "Tôi muốn đổi trả đơn ORD-XXXX"        │
│    ├─ ai-hub cross-session lookup (find order by code)         │
│    ├─ ai-hub queries structmem from Day 1 (preferences)       │
│    └─ POST /v1/orders/{code}/return                            │
│                                                                 │
│  Day 5-7 (compressed to 5 min) — Future purchase                │
│    ├─ User: "Tôi muốn mua thêm áo thun" (no details)         │
│    ├─ ai-hub: "Bạn mua áo size M trắng, muốn size khác?"     │
│    └─ Multi-user isolation: User A ≠ User B's profile          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  New code: 3 services + 1 migration + 4 endpoints             │
│                                                                 │
│  ┌─ app/services/orders_service.py (~200 LOC, NEW) ────────────┐  │
│  │  OrdersService:                                              │  │
│  │    create_order(tenant, user, product, ...) → Order         │  │
│  │    get_by_code(tenant, code) → Order | None                  │  │
│  │    request_return(order_id, reason, serial) → ReturnReq     │  │
│  │    list_user_orders(tenant, user_id) → list[Order]          │  │
│  │  Schema: orders + return_requests tables                     │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ app/services/user_profile_service.py (~150 LOC, NEW) ──────┐  │
│  │  UserProfileService:                                         │  │
│  │    get_preferences(user_id) → {                              │  │
│  │      sizes: ['M'], colors: ['trắng', 'xanh'],              │  │
│  │      price_max: 500000, brands: ['basic'],                   │  │
│  │      categories: ['áo thun'],                                │  │
│  │    }                                                        │  │
│  │    Aggregates structmem items across ALL user_id sessions    │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ app/services/cross_session_memory.py (~120 LOC, NEW) ──────┐  │
│  │  CrossSessionMemory:                                         │  │
│  │    get_recent_messages(user_id, limit=20) → list[Message]    │  │
│  │    get_structmem_for_user(user_id) → list[MemoryItem]        │  │
│  │    Builds "user context" block for system prompt             │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ app/core/database.py (migrations, +60 LOC) ────────────────┐  │
│  │  New tables: orders, return_requests                        │  │
│  │  + Init function for new tables                             │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ app/routes/orders.py (~100 LOC, NEW) ───────────────────────┐  │
│  │  POST /v1/orders                       create order         │  │
│  │  GET  /v1/orders/{code}                lookup by code       │  │
│  │  POST /v1/orders/{code}/return         process return       │  │
│  │  GET  /v1/users/{id}/profile          get preferences      │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ scripts/test_ecommerce_100users.py (~500 LOC, NEW) ─────────┐  │
│  │  Simulates 100 users across 3 sessions                        │  │
│  │  4 success criteria verification                             │  │
│  │  JSON report output                                         │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Data model

```sql
-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    order_code TEXT UNIQUE NOT NULL,  -- e.g. "ORD-2026-0001"
    product_name TEXT NOT NULL,        -- e.g. "Áo thun trắng basic"
    size TEXT,
    color TEXT,
    price INTEGER,
    purchase_date TIMESTAMP NOT NULL,
    status TEXT DEFAULT 'active'       -- active, returned, exchanged
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(order_code);

-- Return requests
CREATE TABLE IF NOT EXISTS return_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    reason TEXT NOT NULL,               -- e.g. "Lỗi chỉ may", "Sai size"
    product_serial TEXT,                -- e.g. "SN-2026-XYZ"
    status TEXT DEFAULT 'pending',      -- pending, approved, rejected, completed
    requested_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP,
    resolution_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_returns_order ON return_requests(tenant_id, order_id);
```

---

## 4. Test scenarios (per user, 3 sessions)

### Session 1: Pre-purchase (Day 1, 5 min total)

| Q# | User message | Expected AI response contains |
|---|---|---|
| 1 | "Có áo thun trắng size M không?" | "có", "áo thun trắng", "M" |
| 2 | "Giá bao nhiêu?" | "250", "250000", "250k" |
| 3 | "Có màu khác không?" | "đen", "xám", "xanh" |
| 4 | "Chất liệu gì?" | "cotton" |
| 5 | "Có co giãn không?" | "có" / "không" |
| 6 | "Bảo hành bao lâu?" | "3 tháng" / "6 tháng" |
| 7 | "Đặt mua 1 cái, mã đơn?" | (AI creates order, returns order_code) |

### Session 2: Return (Day 2-3, 10 min total)

| Q# | User message | Expected AI response |
|---|---|---|
| 1 | "Tôi muốn đổi trả đơn ORD-2026-XXXX" | AI looks up order, returns product info (name, size, color, price) |
| 2 | "Áo bị lỗi chỉ may" | AI creates return request, returns request_id |
| 3 | "Khi nào có hàng đổi?" | AI references "bảo hành 3 tháng" / policy |

### Session 3: Future purchase (Day 5-7, 5 min total)

| Q# | User message | Expected AI response |
|---|---|---|
| 1 | "Tôi muốn mua thêm áo thun" | AI references: "Bạn mua áo thun trắng size M lần trước, muốn size khác? Màu khác?" |
| 2 | "Có size L không?" | AI answers about L, references user's history |
| 3 | "Màu xanh navy có không?" | AI answers |

---

## 5. 4 Success criteria (verified)

| # | Criterion | Verification | Target |
|---|---|---|---|
| 1 | Return/warranty: AI lookup được order bằng mã | Session 2 Q1: AI correctly identifies product from order_code | 90%+ |
| 2 | Cross-session memory: 1+ day sau AI vẫn nhớ preferences | Session 3 Q1: AI mentions "size M" "áo thun trắng" from Day 1 | 70%+ |
| 3 | Personalization: future purchase dùng history | Session 3 Q1: AI asks follow-up aligned with user history | 60%+ |
| 4 | Multi-tenant: 100 users, không leak data | User A's order_code queried by User B returns 404 | 0 leaks |

---

## 6. Data flow (Session 1 → Session 2)

```
Session 1:
  User → POST /v1/chat "Có áo thun trắng size M không?"
    ↓
  ai-hub chat flow:
    - Verbatim memory: empty (no prior)
    - StructMem: empty (no prior)
    - LLM responds with product info
    - Saves to messages table, structmem extraction
    ↓
  User → POST /v1/chat "Đặt mua 1 cái"
    ↓
  ai-hub chat flow:
    - Tools: ai-hub has function calling for create_order
    - (or test script POSTs /v1/orders directly)
    - Creates order with mock product
    - Returns order_code "ORD-2026-0001"

  Sleep 30s (simulating 1 day)

Session 2:
  User → POST /v1/chat "Tôi muốn đổi trả đơn ORD-2026-0001"
    ↓
  ai-hub chat flow:
    - CrossSessionMemory: queries structmem for user_id → finds Day 1 facts
    - VerbatimMemory: queries messages table for user_id → finds 7 messages
    - LLM sees user context + order code → calls GET /v1/orders/ORD-2026-0001
    - Returns product info
    ↓
  User → POST /v1/chat "Áo bị lỗi chỉ may"
    ↓
  ai-hub chat flow:
    - Sees order from previous turn
    - Calls POST /v1/orders/ORD-2026-0001/return
    - Returns return_request_id
```

---

## 7. Test orchestration

| Phase | Time | Description |
|---|---|---|
| 1. Setup | 1 min | Create 100 users, seed RAG, init orders table |
| 2. Session 1 (Q&A) | 5 min | 100 users × 7 questions = 700 turns |
| 3. Inter-session gap | 30s | Sleep (simulates 1 day) |
| 4. Session 2 (return) | 10 min | 100 users × 3 questions = 300 turns |
| 5. Inter-session gap | 30s | Sleep (simulates 3 days) |
| 6. Session 3 (future) | 5 min | 100 users × 3 questions = 300 turns |
| 7. Cross-user leak check | 2 min | User A queries User B's order → must return 404 |
| 8. Report | 30s | Aggregate metrics, write JSON |
| **Total** | ~25 min | |

---

## 8. Error handling

| Failure mode | Handling |
|---|---|
| Order not found by code | Return 404, AI says "Không tìm thấy đơn hàng" |
| Cross-user order query | Return 404 (security), log security event |
| User has no structmem yet | AI gets no preferences, defaults to "size nào? màu gì?" |
| Session 1 didn't create order | Session 2 will fail order lookup → AI asks for order code |
| Concurrent sessions for same user | Lock on user_id in memory aggregation (read-only is fine) |
| Orders table not initialized | Init function in startup, fail fast if not |

---

## 9. Out of scope (YAGNI)

- ❌ Real payment integration (mocked)
- ❌ Real shipping API (mocked)
- ❌ Admin dashboard for orders
- ❌ Refund workflow (just return/exchange)
- ❌ Multi-product orders (1 order = 1 product)
- ❌ Async event-driven notifications
- ❌ User authentication (already in ai-hub)
- ❌ Order history export
- ❌ Real-time order status updates

---

## 10. Files modified/created

**New files (5):**
- `app/services/orders_service.py` (~200 LOC)
- `app/services/user_profile_service.py` (~150 LOC)
- `app/services/cross_session_memory.py` (~120 LOC)
- `app/routes/orders.py` (~100 LOC)
- `scripts/test_ecommerce_100users.py` (~500 LOC)

**Modified files (1):**
- `app/core/database.py` (+60 LOC for new tables)

**Total: ~1130 LOC, 5 new files, 1 modified.**

---

## 11. Open questions

None. All clarified during brainstorming.

Key decisions:
- Scope: Test + new orders table + cross-session memory
- Success: 4 criteria (return lookup 90%, memory 70%, personalization 60%, leak 0)
- Schema: orders + return_requests tables
- Architecture: 3 services + 1 routes file + 1 migration
