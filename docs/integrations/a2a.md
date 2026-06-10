# A2A (Agent2Agent) Integration

AI Hub acts as an **A2A Server**, exposing its LLM/RAG/memory capabilities
to any A2A-compliant client (Google ADK, LangGraph, CrewAI, custom A2A clients,
and — through Captain Custom Tool — Chatwoot).

This is the **standard path** complementing the Chatwoot-specific integration
under `/v1/integrations/chatwoot/*`. Use A2A when integrating with non-Chatwoot
agents; use Chatwoot-specific endpoints when you want Chatwoot's exact
payload format.

## What you get

| Capability | Path |
|---|---|
| **Discovery** | `GET /v1/a2a/agent-card` — JSON manifest of skills, capabilities, auth |
| **SendMessage** | `POST /v1/a2a/jsonrpc` — submit user message, returns Task |
| **GetTask** | `POST /v1/a2a/jsonrpc` (method=GetTask) — poll task status |
| **ListTasks** | `POST /v1/a2a/jsonrpc` (method=ListTasks) — list active tasks |
| **CancelTask** | `POST /v1/a2a/jsonrpc` (method=CancelTask) — cancel in-flight task |
| **Streaming** | Planned (SendStreamingMessage via SSE) — not yet implemented |

## Architecture

```
┌─────────────────────────────────────┐
│ A2A Client                          │
│ - Google ADK Agent                  │
│ - LangGraph workflow                │
│ - Chatwoot Captain Custom Tool      │
│ - Custom Python (a2a-python SDK)    │
└──────────────┬──────────────────────┘
               │ JSON-RPC 2.0 (HTTPS)
               ▼
┌─────────────────────────────────────┐
│ AI Hub                              │
│ GET  /v1/a2a/agent-card             │
│ POST /v1/a2a/jsonrpc                │
│   ├─ SendMessage                    │
│   ├─ GetTask                        │
│   ├─ ListTasks                      │
│   └─ CancelTask                     │
└──────────────┬──────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
   ai_service.chat   Memory (StructMem)
   RAG (cards)       PinnedMemory
   Adaptive routing  Summary
```

## Endpoints

### `GET /v1/a2a/agent-card`

Capability manifest. Clients fetch this first to discover what AI Hub can do.

**Response** (`application/json`):
```json
{
  "name": "AI Hub Fanpage Assistant",
  "description": "Local-first multilingual AI assistant for Vietnamese customer support...",
  "version": "1.0.0",
  "provider": {
    "organization": "AI Hub",
    "url": "https://github.com/haihung2010/ai-hub"
  },
  "url": "https://<ai-hub>/v1/a2a/jsonrpc",
  "preferredTransport": "http+jsonrpc",
  "capabilities": {"streaming": false, "pushNotifications": false},
  "authentication": {
    "schemes": ["apiKey"],
    "credentials": "X-API-KEY header (use the same key as /v1/chat)"
  },
  "skills": [
    {
      "id": "fanpage_chat",
      "name": "Fanpage Chat (General)",
      "description": "Conversational AI in Vietnamese. Answers product questions...",
      "examples": ["Giá sản phẩm A là bao nhiêu?", "Chính sách đổi trả trong 7 ngày..."],
      "tags": ["chat", "vietnamese", "fanpage", "rag"]
    },
    {
      "id": "product_lookup",
      "name": "Product Catalog Search",
      "description": "Search the fanpage product catalog. Returns up to 3 matching products...",
      "examples": ["Tìm serum vitamin C", "Có sản phẩm nào dưỡng ẩm cho da khô?"],
      "tags": ["product", "search", "rag", "ecommerce"]
    },
    {
      "id": "escalate_human",
      "name": "Escalate to Human",
      "description": "Log an escalation event and return a ticket_id for handoff...",
      "tags": ["escalation", "handoff", "support"]
    }
  ],
  "defaultInputModes": ["text"],
  "defaultOutputModes": ["text"]
}
```

### `POST /v1/a2a/jsonrpc`

Single JSON-RPC 2.0 endpoint. Method dispatched by the `method` field.

#### Method: `SendMessage`

Submit a user message. Returns a Task with the agent's reply.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "Giá sản phẩm A là bao nhiêu?"}]
    },
    "contextId": "1",
    "configuration": {"acceptedOutputModes": ["text"], "blocking": true}
  }
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "result": {
    "id": "a2a-abc123def456",
    "contextId": "1",
    "status": {
      "state": "completed",
      "message": {
        "role": "agent",
        "parts": [{"kind": "text", "text": "Sản phẩm A hiện có giá 450,000 VND..."}],
        "contextId": "1"
      }
    },
    "history": [
      {"role": "user", "parts": [{"kind": "text", "text": "Giá sản phẩm A?"}], "contextId": "1"},
      {"role": "agent", "parts": [{"kind": "text", "text": "Sản phẩm A hiện có giá 450,000 VND..."}], "contextId": "1"}
    ],
    "artifacts": [
      {
        "name": "assistant_reply",
        "description": "AI Hub chat completion",
        "parts": [{"kind": "text", "text": "Sản phẩm A hiện có giá 450,000 VND..."}],
        "index": 0
      }
    ]
  }
}
```

**Continuation** (multi-turn dialog): include the previous task's `id` in the new SendMessage:
```json
{
  "jsonrpc": "2.0",
  "id": "req-2",
  "method": "SendMessage",
  "params": {
    "id": "a2a-abc123def456",   // ← continue previous task
    "message": {"role": "user", "parts": [{"kind": "text", "text": "Còn loại nào rẻ hơn?"}]}
  }
}
```

#### Method: `GetTask`

```json
{"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": "a2a-abc123def456"}}
```

Returns the same Task structure as SendMessage. Use for polling.

#### Method: `ListTasks`

```json
{"jsonrpc": "2.0", "id": 3, "method": "ListTasks", "params": {}}
```

Returns `{"tasks": [...]}` — all **non-terminal** tasks (submitted, working, input-required).

#### Method: `CancelTask`

```json
{"jsonrpc": "2.0", "id": 4, "method": "CancelTask", "params": {"id": "a2a-abc123def456"}}
```

Marks the task as CANCELED. Returns the updated task. Returns error if task
is already in a terminal state (completed/failed/canceled/rejected).

### Error codes

JSON-RPC 2.0 standard + A2A-specific:

| Code | Meaning |
|---|---|
| `-32700` | Parse error (invalid JSON) |
| `-32600` | Invalid request (missing jsonrpc/method/id) |
| `-32601` | Method not found |
| `-32602` | Invalid params (missing/wrong fields) |
| `-32603` | Internal error (AI Hub exception) |
| `-32001` | Task not found |
| `-32002` | Content-Type not supported |
| `-32003` | Unsupported operation (e.g. cancel a completed task) |

Errors are returned with HTTP 200 (JSON-RPC convention), with the `error`
object in the response body.

## Multi-tenant mapping

| A2A field | AI Hub field | Convention |
|---|---|---|
| `contextId` | `tenant_id` | `cw_<id>` if numeric, else `<id>` as-is |
| (none) | `user_name` | `a2a_<contextId>` (for memory continuity) |
| `params.id` (continuation) | `session_id` | Same as A2A task id — re-using the id across SendMessages keeps memory in scope |

## Auth

`X-API-KEY` header (same as `/v1/chat` and `/v1/integrations/chatwoot/*`).
Future A2A versions MAY add OAuth/JWT support — see AgentCard's
`authentication.schemes` field.

## Examples

### curl: Discover + Send

```bash
# 1. Discover
curl -H "X-API-KEY: $KEY" https://<ai-hub>/v1/a2a/agent-card | jq .

# 2. Send a message
curl -X POST -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "SendMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Giá sản phẩm A?"}]
      }
    }
  }' \
  https://<ai-hub>/v1/a2a/jsonrpc
```

### Python (using a2a-python SDK)

```python
from a2a.client import A2AClient

client = A2AClient(
    url="https://<ai-hub>/v1/a2a/jsonrpc",
    headers={"X-API-KEY": "<your-key>"},
)

# Discover
card = await client.discover_agent_card()
print(f"Available skills: {[s.id for s in card.skills]}")

# Send
task = await client.send_message(
    message={
        "role": "user",
        "parts": [{"kind": "text", "text": "Giá sản phẩm A?"}],
    }
)
print(f"Reply: {task.artifacts[0].parts[0].text}")
```

### Chatwoot Captain Custom Tool → AI Hub A2A

Chatwoot Captain Custom Tool is just a declarative HTTP call. Point it at
`/v1/a2a/jsonrpc` with the right body template:

```json
{
  "name": "ai_hub_via_a2a",
  "description": "Use when the customer asks a Vietnamese question about the fanpage. AI Hub has RAG over our product catalog.",
  "method": "POST",
  "url": "https://<ai-hub>/v1/a2a/jsonrpc",
  "headers": {"X-API-KEY": "<your-key>"},
  "body": {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "{{input}}"}]
      }
    }
  },
  "response_variable": "result.artifacts[0].parts[0].text"
}
```

This way Chatwoot Captain can call AI Hub through the **standard A2A
protocol** instead of the Chatwoot-specific endpoints. Better for the long
run since it can be reused with any other A2A client.

## Task lifecycle

```
submitted → working → completed
                  ↘ failed
                  ↘ input-required (waiting on user)
                  ↘ auth-required (not yet used)
                  ↘ rejected
                  ↘ canceled
```

`completed`, `failed`, `canceled`, `rejected` are **terminal**. Tasks in a
terminal state cannot accept further messages and cannot be cancelled.

In-memory task store, TTL 1 hour, not persisted. Production should swap to
Redis or PostgreSQL for durability.

## Limitations (v1.0)

- **No streaming** (`SendStreamingMessage` / SSE). Roadmap item — see
  [issue tracker](#) for ETA.
- **No push notifications** (`tasks/pushNotification`).
- **In-memory task store** (lost on restart). Swap to Redis for prod.
- **X-API-KEY only** (no OAuth/JWT).
- **No `auth-required` state** — anonymous requests with valid X-API-KEY
  are accepted.

## References

- A2A spec: https://a2a-protocol.org/latest/specification/
- A2A Python SDK: https://github.com/a2aproject/a2a-python
- A2A samples: https://github.com/a2aproject/a2a-samples
- AI Hub source: `app/models/a2a.py`, `app/services/a2a_integration.py`,
  `app/routes/a2a.py`
- AI Hub tests: `tests/unit/test_a2a.py` (13 tests, all pass)
- Chatwoot integration (alternative): `docs/integrations/chatwoot.md`
