# Chatwoot Integration

AI Hub exposes two HTTP endpoints that let Chatwoot (the open-source customer
support platform) use AI Hub as its LLM backend. With this integration, a
Chatwoot agent inbox can leverage AI Hub's local LLM serving, RAG, and
memory features.

## What you get

| Capability | How |
|---|---|
| **AI Assistant auto-reply** | Configure a Chatwoot Captain Custom Tool pointing at AI Hub; Captain calls AI Hub synchronously and uses the response as the bot's reply. |
| **Co-Pilot draft** | Same Custom Tool, but in Co-Pilot mode Captain shows the response to the human agent for review before sending. |
| **Async AgentBot** | Register AI Hub as a Chatwoot AgentBot; AI Hub receives every incoming customer message, computes a reply, and POSTs it back to Chatwoot via the conversation's `message_url`. |
| **Multi-tenant isolation** | Each Chatwoot account maps to a distinct AI Hub `tenant_id` (prefixed `cw_<account_id>`). |
| **Session resume** | `conversation.id` becomes AI Hub `session_id` (`cw-conv-<id>`), so the conversation history and memory persist across Captain calls. |
| **HMAC verification** | Optional X-Chatwoot-Signature header (HMAC-SHA256 of raw body) prevents unauthorized access. |

## Architecture

```
                 Chatwoot UI
                       │
   ┌───────────────────┼────────────────────┐
   │                   │                    │
   ▼                   ▼                    ▼
 Captain         Custom Tool            AgentBot
 (LLM            (declarative           (webhook
  fallback)       HTTP action)           callback)
   │                   │                    │
   │            POST /v1/integrations/       │
   │            chatwoot/respond            │
   │                   │              POST /v1/integrations/
   │                   │              chatwoot/agent_bot
   │                   │                    │
   └─────────┬─────────┴────────────────────┘
             ▼
        AI Hub (FastAPI)
             │
   ┌─────────┼──────────┐
   ▼         ▼          ▼
 Llama.cpp  RAG       Memory
 12B Q4     (70% vec (StructMem +
            + 30% tok)  Pinned)
```

## Endpoints

### `POST /v1/integrations/chatwoot/respond`

Synchronous endpoint for **Captain Custom Tool**. Returns the AI reply in the
same HTTP response.

**Request** (`application/json`):
```json
{
  "messages": [
    {"role": "user", "content": "Giá sản phẩm A là bao nhiêu?"}
  ],
  "conversation": {
    "id": 42,
    "display_id": 100,
    "status": "open"
  },
  "contact": {
    "id": 7,
    "name": "Anh Tuấn",
    "email": "tuan@example.com"
  },
  "account": {
    "id": 1,
    "name": "Acme Corp"
  },
  "project_id": "fanpage",     // optional, override AI Hub project
  "tenant_id": "acme_prod"     // optional, override default cw_<account_id>
}
```

**Response** (`application/json`):
```json
{
  "response": "Sản phẩm A hiện có giá 450,000 VND. Bạn muốn mình tư vấn thêm không?",
  "model": "local-gemma4-e2b-q4-bg",
  "tokens_in": 219,
  "tokens_out": 56,
  "latency_ms": 1142
}
```

### `POST /v1/integrations/chatwoot/agent_bot`

Async endpoint for **AgentBot webhook**. Returns 200 immediately; AI Hub
posts the reply back to Chatwoot via the `message_url` field.

**Request**:
```json
{
  "event": "message_created",
  "message": {
    "id": 100,
    "content": "Giá sản phẩm A?",
    "message_type": "incoming"
  },
  "conversation": {
    "id": 42,
    "account_id": 1,
    "inbox_id": 5,
    "message_url": "https://app.chatwoot.com/api/v1/accounts/1/conversations/42/messages"
  },
  "sender": {
    "id": 7,
    "name": "Anh Tuấn",
    "type": "contact"
  }
}
```

**Response** (always 200 unless signature invalid):
```json
{
  "status": "queued"
}
```

Outgoing messages (from agent) and empty messages are skipped (no llama call,
no callback) — returns `{"status": "skipped", "reason": "..."}`.

### `GET /v1/integrations/chatwoot/health`

Health probe (no auth). Returns which env vars are configured:
```json
{
  "status": "ok",
  "webhook_secret_configured": true,
  "api_token_configured": true
}
```

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `X-API-KEY` (request header) | Yes (always) | AI Hub auth. Reuse the master key or a per-tenant key. |
| `CHATWOOT_WEBHOOK_SECRET` | Optional but **strongly recommended for production** | HMAC-SHA256 secret for `X-Chatwoot-Signature` header. If unset, signature verification is skipped (dev mode only). |
| `CHATWOOT_API_TOKEN` | Required only for AgentBot | Token used by AI Hub to POST replies back to Chatwoot's `message_url`. Get from Chatwoot → Profile Settings → Access Token. |

## Three specialised Custom Tools

In addition to the general `/respond` endpoint, AI Hub exposes three
specialised tools that Captain can call declaratively (each is a
separate Custom Tool in the Chatwoot UI):

| Tool | When to call | Endpoint |
|---|---|---|
| `ai_hub_product_lookup` | Customer asks about a specific product, ingredient, or policy | `POST /v1/integrations/chatwoot/tools/product_lookup` |
| `ai_hub_order_status` | Customer asks "where's my order" or "đơn hàng của tôi đâu" | `POST /v1/integrations/chatwoot/tools/order_status` (STUB) |
| `ai_hub_escalate_human` | Customer is upset, asks for refund > threshold, or explicitly requests a human | `POST /v1/integrations/chatwoot/tools/escalate_human` |

### Tool 1: product_lookup

**Request**:
```json
{
  "query": "Serum Vitamin C 20%",
  "tenant_id": "acme_prod",      // optional, defaults to "cw_default"
  "project_id": "fanpage",      // optional, defaults to "fanpage"
  "knowledge_domain": "products",  // optional: "products" | "policies" | "promotions"
  "limit": 3                    // optional, 1-10, default 3
}
```

**Response**:
```json
{
  "found": true,
  "query": "Serum Vitamin C 20%",
  "products": [
    {
      "title": "Serum Vitamin C 20% (Sản phẩm A)",
      "summary": "Serum Vitamin C nồng độ 20% cho da xỉn màu...",
      "content": "Tên: Serum Vitamin C 20% (sản phẩm A)\nGiá: 450,000 VND / 30ml\n...",
      "score": 0.85,
      "knowledge_domain": "products",
      "tags": ["vitamin-c", "serum", "chống-lão-hóa"]
    }
  ]
}
```

If the RAG is disabled or no card matches, returns `{"found": false, "products": []}`.

**Chatwoot Custom Tool config** (Captain → Tools → Add):
```json
{
  "name": "ai_hub_product_lookup",
  "description": "Search the fanpage product catalog and policies. Use when the customer asks about a specific product ('Giá Serum Vitamin C?'), a policy ('Chính sách đổi trả?'), or a promotion ('Có khuyến mãi gì?').",
  "method": "POST",
  "url": "https://<ai-hub>/v1/integrations/chatwoot/tools/product_lookup",
  "headers": {
    "X-API-KEY": "<your-ai-hub-key>"
  },
  "body": {
    "query": "{{input}}",
    "limit": 3
  },
  "response_variable": "products"
}
```

### Tool 2: order_status (STUB)

> ⚠️ **STUB**: AI Hub has no orders table. For production, wire
> `chatwoot_integration.order_status_lookup()` to your real order backend
> (Shopify GraphQL, custom DB, ERP API, etc.).

**Request**:
```json
{
  "order_id": "ORD-12345",
  "contact_email": "test@example.com",  // optional
  "tenant_id": "acme_prod"               // optional
}
```

**Response** (always `found: false` until production integration):
```json
{
  "found": false,
  "order_id": "ORD-12345",
  "status": "not_configured",
  "details": {
    "stub_reason": "AI Hub does not have an orders table. Wire order_status_lookup() in chatwoot_integration.py to your order backend (Shopify, ERP, etc.)",
    "request_email": "test@example.com"
  },
  "message": "Hệ thống AI chưa được kết nối với hệ thống đơn hàng. Vui lòng chuyển cho nhân viên kiểm tra đơn hàng #ORD-12345."
}
```

**Chatwoot Custom Tool config**:
```json
{
  "name": "ai_hub_order_status",
  "description": "Look up the status of a customer order. Use when the customer mentions an order number, tracking ID, or asks 'when will my order arrive?'. NOTE: Currently a stub — AI Hub returns a handoff message.",
  "method": "POST",
  "url": "https://<ai-hub>/v1/integrations/chatwoot/tools/order_status",
  "headers": {"X-API-KEY": "<your-ai-hub-key>"},
  "body": {
    "order_id": "{{input}}"
  },
  "response_variable": "message"
}
```

**Production hook (Shopify example)** — replace the stub in
`app/services/chatwoot_integration.py:order_status()`:
```python
async def order_status(order_id, contact_email, tenant_id):
    shopify = ShopifyClient(tenant_id=tenant_id)
    order = await shopify.get_order(order_id)
    if not order:
        return {"found": False, "order_id": order_id, "status": "not_found", ...}
    return {
        "found": True,
        "order_id": order_id,
        "status": order.fulfillment_status,  # "pending" | "shipped" | "delivered"
        "details": {
            "tracking_url": order.tracking_url,
            "carrier": order.carrier,
            "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
            "items": order.line_items,
        },
        "message": f"Đơn hàng {order_id}: {order.fulfillment_status}. Vận chuyển bởi {order.carrier}.",
    }
```

### Tool 3: escalate_human

**Request**:
```json
{
  "conversation_id": 42,
  "reason": "Customer wants a refund > $50",
  "contact_id": 7,                  // optional
  "contact_name": "Anh Tuấn",       // optional
  "priority": "high",                // "low" | "medium" (default) | "high" | "urgent"
  "tenant_id": "acme_prod",          // optional
  "project_id": "fanpage"           // optional
}
```

**Response**:
```json
{
  "escalated": true,
  "ticket_id": "CW-ESC-A1B2C3D4",
  "assigned_agent": null,
  "estimated_response_minutes": 15,
  "message": "Đã chuyển yêu cầu cho nhân viên hỗ trợ (mã: CW-ESC-A1B2C3D4). Nhân viên sẽ phản hồi trong khoảng 15 phút. Cảm ơn bạn đã kiên nhẫn!"
}
```

**ETA by priority**:
- `urgent` → 5 min
- `high` → 15 min
- `medium` → 30 min (default)
- `low` → 60 min

**Chatwoot Custom Tool config**:
```json
{
  "name": "ai_hub_escalate_human",
  "description": "Hand off the conversation to a human agent. Use when the customer is upset, asks for a refund above the auto-approve threshold, says 'talk to a person', or when Captain cannot confidently answer.",
  "method": "POST",
  "url": "https://<ai-hub>/v1/integrations/chatwoot/tools/escalate_human",
  "headers": {"X-API-KEY": "<your-ai-hub-key>"},
  "body": {
    "conversation_id": "{{conversation.id}}",
    "reason": "{{input}}",
    "contact_id": "{{contact.id}}",
    "contact_name": "{{contact.name}}",
    "priority": "high"
  },
  "response_variable": "message"
}
```

**Database**: AI Hub auto-creates an `escalation_events` table on first call.
Schema:

```sql
CREATE TABLE escalation_events (
    id              BIGSERIAL PRIMARY KEY,
    ticket_id       TEXT NOT NULL UNIQUE,
    tenant_id       TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    conversation_id BIGINT NOT NULL,
    contact_id      BIGINT,
    contact_name    TEXT,
    reason          TEXT NOT NULL,
    priority        TEXT NOT NULL DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_escalation_tenant_conv ON escalation_events (tenant_id, conversation_id);
CREATE INDEX idx_escalation_status ON escalation_events (tenant_id, status) WHERE status = 'open';
```

**Production extensions** — modify `escalate_human()` in
`app/services/chatwoot_integration.py`:
- Push to Slack `#customer-escalations`
- Send email to on-call agent
- Create Linear ticket
- Trigger Chatwoot conversation assignment

## Chatwoot setup (self-hosted, Captain Custom Tool)

### 1. Add AI Hub as a Custom Tool

In Chatwoot: **Settings → Captain → Tools → Add a new tool**

Fill in:
- **Name**: `ai_hub_chat`
- **Description**: "Respond to a customer message using AI Hub's local LLM. Use this when the customer asks a product, pricing, or policy question."
- **HTTP method**: `POST`
- **Endpoint URL**: `https://<ai-hub-host>/v1/integrations/chatwoot/respond`
- **Auth header** (if your AI Hub requires a per-tenant key):
  ```
  X-API-KEY: <your-ai-hub-key>
  ```
- **Request body** (JSON):
  ```json
  {
    "messages": [
      {{#each conversation.messages}}
      {"role": "{{role}}", "content": "{{content}}"}
      {{/each}}
    ],
    "conversation": {
      "id": {{conversation.id}},
      "display_id": {{conversation.display_id}},
      "status": "{{conversation.status}}"
    },
    "contact": {
      "id": {{contact.id}},
      "name": "{{contact.name}}",
      "email": "{{contact.email}}"
    },
    "account": {
      "id": {{account.id}},
      "name": "{{account.name}}
    }
  }
  ```
- **Response variable**: `response`

### 2. Configure the Assistant or Co-Pilot to use it

In Captain → **Assistant configuration**:
- **Action**: `ai_hub_chat` (the tool you just added)
- Guardrails: `ai_hub_chat` should not be called for greetings or casual chat; configure Captain with a guard like "Use `ai_hub_chat` only when the customer asks a specific product or policy question."

For Co-Pilot: same tool, but it shows the response to the human agent for review before sending.

### 3. (Optional) HMAC signature

If you set `CHATWOOT_WEBHOOK_SECRET` on AI Hub, configure the same secret in
Chatwoot's Custom Tool settings so Chatwoot signs every request with
`X-Chatwoot-Signature: <sha256 hex>`.

## Chatwoot setup (self-hosted, AgentBot)

### 1. Create an AgentBot in Chatwoot

Chatwoot → **Settings → Agents → Agent Bots → Add a new bot**:
- **Name**: `AI Hub Bot`
- **Description**: "Routes customer messages to AI Hub's local LLM"
- **Outgoing URL**: `https://<ai-hub-host>/v1/integrations/chatwoot/agent_bot`
- **Bot type**: `Webhook`

### 2. Assign the bot to an Inbox

Inbox → Settings → **Agent bots** → Add `AI Hub Bot` to the inbox.

### 3. Set the AI Hub env

```bash
export CHATWOOT_API_TOKEN="<chatwoot-access-token>"
export CHATWOOT_WEBHOOK_SECRET="<random-256-bit-string>"  # optional
```

## Multi-tenant mapping

| Chatwoot field | AI Hub field | Convention |
|---|---|---|
| `account.id` (e.g. `1`) | `tenant_id` | `cw_<id>` (e.g. `cw_1`) — override via payload `tenant_id` |
| `conversation.id` (e.g. `42`) | `session_id` | `cw-conv-<id>` (e.g. `cw-conv-42`) — for memory and history resume |
| `contact.id` (e.g. `7`) | `user_name` | `cw_contact_<id>` — for per-user memory |
| `sender.id` + `sender.type` | `user_name` (fallback) | `cw_<type>_<id>` (e.g. `cw_contact_7`) |

## Security checklist

- [ ] `CHATWOOT_WEBHOOK_SECRET` set in production (HMAC verification)
- [ ] AI Hub exposed via HTTPS (not raw HTTP) in production
- [ ] `X-API-KEY` is a per-tenant key, not the master key
- [ ] Rate limit per Chatwoot account enforced at the AI Hub rate limiter
- [ ] Logs do not log `X-Chatwoot-Signature` or `X-API-KEY` header values
- [ ] Chatwoot → AI Hub network is private (VPN or VPC peering) if possible

## Test with curl

### Health
```bash
curl https://<ai-hub>/v1/integrations/chatwoot/health
```

### Custom Tool (synchronous)
```bash
curl -X POST https://<ai-hub>/v1/integrations/chatwoot/respond \
  -H "X-API-KEY: <your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Giá sản phẩm A là bao nhiêu?"}],
    "conversation": {"id": 1, "status": "open"},
    "contact": {"id": 1, "name": "Test"},
    "account": {"id": 1, "name": "Test"}
  }'
```

### AgentBot (async, AI Hub POSTs back)
```bash
curl -X POST https://<ai-hub>/v1/integrations/chatwoot/agent_bot \
  -H "X-API-KEY: <your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "event": "message_created",
    "message": {"id": 1, "content": "Giá sản phẩm A?", "message_type": "incoming"},
    "conversation": {"id": 1, "account_id": 1, "message_url": "https://app.chatwoot.com/api/v1/accounts/1/conversations/1/messages"},
    "sender": {"id": 1, "name": "Test", "type": "contact"}
  }'
```

## Troubleshooting

### `404 Not Found` on the endpoint
- Verify the AI Hub was restarted after pulling the latest code (the router
  is registered in `app/main.py:app.include_router(chatwoot_routes.router)`)
- Verify the route prefix: `/v1/integrations/chatwoot/...` (not `/v1/chatwoot/...`)

### `401 Unauthorized` even with the correct X-API-KEY
- AI Hub middleware auth uses the `X-API-KEY` header. Make sure your Chatwoot
  Custom Tool is configured to send this header on every request.

### `401 Invalid X-Chatwoot-Signature`
- The `CHATWOOT_WEBHOOK_SECRET` on AI Hub must match the secret used by
  Chatwoot to sign the body
- Signature is HMAC-SHA256 of the **raw** request body (not the re-serialized
  JSON), so make sure Chatwoot signs before serializing

### AgentBot: reply never appears in Chatwoot
- Verify `CHATWOOT_API_TOKEN` is set on AI Hub
- Verify the token has permission to POST to `conversation.message_url`
- Check `/tmp/aihub-logs/uvicorn.log` for `Failed to send Chatwoot reply`
- Try with a manual `curl` to the `message_url` using the same token to
  isolate whether the issue is the token or AI Hub

### Custom Tool: Captain says "Tool returned no response"
- The `response` field must be a string (even empty). AI Hub always returns
  a string. If Captain says "no response", check the `response` key path in
  your Custom Tool config — it should be exactly `response`, not `data.response`

### Tests fail with `RESPX: ... not mocked!` from HuggingFace
- The FastEmbed model tries to download on first RAG use. AI Hub disables RAG
  in tests via the `ENABLE_KNOWLEDGE_RAG=False` test fixture (added 2026-06-09)
- For the live server, ensure FastEmbed has downloaded the model at least once
  (run any chat request with RAG enabled and a knowledge-bearing query first)

## References

- AI Hub source: `app/models/chatwoot.py`, `app/services/chatwoot_integration.py`, `app/routes/chatwoot_webhook.py`
- Tests: `tests/unit/test_chatwoot_webhook.py` (15 tests covering payload mapping, HMAC, callback flow, error paths)
- Chatwoot API docs: https://developers.chatwoot.com/api-reference/introduction
- Chatwoot Captain Custom Tools blog: https://www.chatwoot.com/blog/captain-custom-tools/
