# Vehix → AI Hub Integration Guide

This guide explains how the Vehix team should integrate with AI Hub in **dev/test** so developers can connect the Vehix chatbot to the local AI Hub API quickly and consistently.

---

## 1. Integration goal

Vehix should call AI Hub as the central chat backend for:
- chatbot question/answer flows
- session resume by user name
- optional Lite-mode image input
- optional web search toggle

The target integration is:
- Vehix frontend or backend sends requests to AI Hub
- AI Hub handles security, session memory, prompt routing, and model calls
- Vehix renders the returned answer in its chatbot UI

---

## 2. Base URLs by environment

### Local dev
- AI Hub UI/API: `http://localhost:8000`

### Public/staging-style endpoint
- AI Hub public endpoint: `https://api-aiserver.htechlabsvn.com/`

For Vehix **dev/test integration**, start with:
- `http://localhost:8000`

---

## 3. Required authentication

All protected API calls must include:

```http
X-API-KEY: <your-api-key>
Content-Type: application/json
```

### Notes
- API key is required for `/v1/chat`
- API key is also required for `/v1/users/{user_name}/sessions`
- If the key is missing or invalid, AI Hub returns `401`
- Default rate limit is `5` requests/minute per API key

---

## 4. Main API endpoints for Vehix

### 4.1 Chat endpoint

**Endpoint**

```http
POST /v1/chat
```

**Purpose**
- send a user message to AI Hub
- optionally resume an old session
- optionally attach images in Lite mode
- get the assistant reply

---

### 4.2 Session resume endpoint

**Endpoint**

```http
GET /v1/users/{user_name}/sessions?project_id=vehix&tenant_id=<tenant>
```

**Purpose**
- fetch resumable sessions for a Vehix user
- restore the latest session in the chatbot UI

---

## 5. Chat request contract

AI Hub request schema is based on `app/models/chat.py`.

### Request body

```json
{
  "project_id": "vehix",
  "tenant_id": "vehix-dev",
  "user_name": "hung",
  "user_message": "Hiện tại có bao nhiêu hợp đồng đang thuê?",
  "images": null,
  "history": [],
  "session_id": null,
  "stream": false,
  "provider": null,
  "model_mode": "lite",
  "enable_search": false
}
```

### Field meanings

| Field | Required | Type | Description |
|---|---|---:|---|
| `project_id` | yes | string | Must identify the integration project. For Vehix use `vehix`. |
| `tenant_id` | yes | string | Tenant/environment scope. Recommend explicit value like `vehix-dev` or actual tenant ID. |
| `user_name` | no | string | Stable display/user identifier used for session resume. |
| `user_message` | yes | string | The current user prompt/question. |
| `images` | no | string[] | Base64 image list. Only use in `lite` mode. |
| `history` | no | Message[] | Prior messages if caller wants to provide explicit history. Usually keep `[]` and use `session_id` instead. |
| `session_id` | no | string | Existing session to continue. If omitted, AI Hub creates a new one. |
| `stream` | no | boolean | Must currently be `false`. Streaming is not implemented yet. |
| `provider` | no | string | Optional. Usually leave `null`. |
| `model_mode` | no | `normal` or `lite` | Use `lite` for dev chatbot speed and image support. |
| `enable_search` | no | boolean | Enable web search injection for current-information queries. |

---

## 6. Chat response contract

### Example response

```json
{
  "project_id": "vehix",
  "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
  "model": "gemma4:e4b",
  "provider": "local",
  "content": "Hiện tại có 3 hợp đồng đang ở trạng thái ACTIVE.",
  "user_id": "0b0df11d-3e58-4df3-8f53-46a5a32d1c9f"
}
```

### Field meanings

| Field | Type | Description |
|---|---:|---|
| `project_id` | string | The resolved project ID |
| `session_id` | string | Persist this in Vehix to continue the same chat |
| `model` | string | Actual model used |
| `provider` | string | Usually `local` |
| `content` | string | Assistant response to display in chatbot |
| `user_id` | string/null | Internal AI Hub user identifier |

---

## 7. Session resume API

### Request

```http
GET /v1/users/hung/sessions?project_id=vehix&tenant_id=vehix-dev
X-API-KEY: dev-api-key
```

### Example response

```json
[
  {
    "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
    "created_at": "2026-04-26 10:30:00",
    "last_message_preview": "Hiện tại có 3 hợp đồng đang ở trạng thái ACTIVE."
  }
]
```

### Recommended behavior in Vehix
- when user opens chatbot:
  1. read saved `user_name`
  2. call session resume endpoint
  3. if sessions exist, take the newest one
  4. store `session_id`
  5. continue chat with that session ID
- if no sessions exist:
  - create a fresh session by calling `/v1/chat` without `session_id`

---

## 8. Recommended Vehix integration flow

```text
[Vehix user opens chatbot]
        |
        +--> load saved user_name / apiKey / sessionId
        |
        +--> if user_name exists:
        |      GET /v1/users/{user_name}/sessions?project_id=vehix&tenant_id=...
        |      -> restore latest session_id if available
        |
        +--> user sends message
               POST /v1/chat
               {
                 project_id: "vehix",
                 tenant_id: "vehix-dev",
                 user_name,
                 user_message,
                 session_id,
                 model_mode: "lite",
                 enable_search: false
               }
        |
        +--> AI Hub returns ChatResponse
        |
        +--> Vehix stores returned session_id
        |
        +--> Vehix renders content in chatbot UI
```

---

## 9. Recommended settings for Vehix dev/test

Use these defaults first:

```json
{
  "project_id": "vehix",
  "tenant_id": "vehix-dev",
  "model_mode": "lite",
  "enable_search": false,
  "stream": false
}
```

### Why
- `lite` is faster for dev/test and supports images
- `enable_search: false` avoids unnecessary web lookup noise during business-data testing
- `stream: false` is required because streaming is not implemented yet

---

## 10. cURL examples

### 10.1 Start a new chat

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-KEY: dev-api-key' \
  -d '{
    "project_id": "vehix",
    "tenant_id": "vehix-dev",
    "user_name": "hung",
    "user_message": "Cho tôi danh sách xe đang trống",
    "model_mode": "lite",
    "enable_search": false,
    "stream": false
  }'
```

### 10.2 Continue an existing chat

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-KEY: dev-api-key' \
  -d '{
    "project_id": "vehix",
    "tenant_id": "vehix-dev",
    "user_name": "hung",
    "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
    "user_message": "Còn hợp đồng nào active không?",
    "model_mode": "lite",
    "enable_search": false,
    "stream": false
  }'
```

### 10.3 Get latest sessions for a user

```bash
curl 'http://localhost:8000/v1/users/hung/sessions?project_id=vehix&tenant_id=vehix-dev' \
  -H 'X-API-KEY: dev-api-key'
```

---

## 11. JavaScript fetch example for Vehix frontend

```ts
const AI_HUB_URL = 'http://localhost:8000';
const API_KEY = 'dev-api-key';

async function sendVehixChat({
  userName,
  tenantId,
  sessionId,
  userMessage,
}: {
  userName: string;
  tenantId: string;
  sessionId?: string | null;
  userMessage: string;
}) {
  const response = await fetch(`${AI_HUB_URL}/v1/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-KEY': API_KEY,
    },
    body: JSON.stringify({
      project_id: 'vehix',
      tenant_id: tenantId,
      user_name: userName,
      session_id: sessionId ?? null,
      user_message: userMessage,
      model_mode: 'lite',
      enable_search: false,
      stream: false,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail || `AI Hub request failed (${response.status})`);
  }

  return response.json();
}
```

---

## 12. JavaScript session restore example

```ts
async function restoreVehixSession(userName: string, tenantId: string) {
  const response = await fetch(
    `http://localhost:8000/v1/users/${encodeURIComponent(userName)}/sessions?project_id=vehix&tenant_id=${encodeURIComponent(tenantId)}`,
    {
      headers: {
        'X-API-KEY': 'dev-api-key',
      },
    },
  );

  if (!response.ok) {
    throw new Error(`Failed to restore session (${response.status})`);
  }

  const sessions = await response.json();
  return sessions.length > 0 ? sessions[0].session_id : null;
}
```

---

## 13. Image support rules

Vehix may send images only when:
- `model_mode = "lite"`
- images are passed as Base64 strings in `images`

### Example

```json
{
  "project_id": "vehix",
  "tenant_id": "vehix-dev",
  "user_name": "hung",
  "user_message": "Đọc giúp tôi ảnh giấy tờ này",
  "model_mode": "lite",
  "images": [
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
  ],
  "enable_search": false,
  "stream": false
}
```

### Important
- do **not** send images in `normal` mode
- use Lite mode for multimodal input only

---

## 14. Error handling guide

### Common response codes

| Code | Meaning | Recommended Vehix behavior |
|---|---|---|
| `200` | Success | Render chatbot response |
| `401` | Invalid/missing API key | Prompt developer/user to configure API key |
| `429` | Rate limit exceeded | Retry later, show friendly throttle message |
| `502` | Upstream/model error | Show temporary failure and allow retry |
| `503` | Ollama unavailable | Show service unavailable message |
| `504` | Upstream timeout | Show timeout and allow retry |
| `501` | Streaming not implemented | Force non-stream mode |

### Example UI-safe fallback

```ts
try {
  const result = await sendVehixChat(...);
  renderAssistantMessage(result.content);
} catch (error) {
  renderAssistantMessage('AI Hub is temporarily unavailable. Please try again.');
}
```

---

## 15. Recommended dev/test implementation checklist

### AI Hub side
- [ ] AI Hub is running on `http://localhost:8000`
- [ ] Ollama is available
- [ ] API key is known by Vehix dev team
- [ ] `project_id=vehix` is used consistently
- [ ] CORS allows Vehix origin

### Vehix side
- [ ] Save `user_name`
- [ ] Save returned `session_id`
- [ ] Always send `X-API-KEY`
- [ ] Default to `model_mode=lite` for dev/test
- [ ] Keep `stream=false`
- [ ] Implement resume flow via `/v1/users/{user_name}/sessions`
- [ ] Handle `401`, `429`, `502`, `503`, `504` gracefully

---

## 16. Suggested integration profile for Vehix

### Dev
- Base URL: `http://localhost:8000`
- `project_id`: `vehix`
- `tenant_id`: `vehix-dev`
- `model_mode`: `lite`
- `enable_search`: `false`

### Test/UAT
- Base URL: shared test AI Hub endpoint
- `project_id`: `vehix`
- `tenant_id`: `vehix-test`
- `model_mode`: `lite` or `normal` depending on test goal
- `enable_search`: usually `false` for business-data validation

---

## 17. Recommendation for Vehix chatbot architecture

For Vehix, the cleanest path is:

```text
Vehix Web UI
   -> Next.js/NestJS integration layer (optional)
   -> AI Hub /v1/chat
   -> AI Hub handles model + memory + session
   -> Vehix renders answer
```

### Recommended practice
- keep AI Hub as the chat orchestration layer
- keep Vehix business-data lookup logic in Vehix-side API adapters or project prompt design
- store only lightweight chat state in Vehix UI: `user_name`, `session_id`, `api key` if needed for dev tooling

---

## 18. Next recommended documents

After this Vehix guide, the next useful docs are:
1. **Generic project integration guide** for all future projects
2. **Production integration guide** with environment separation and API key policy
3. **Prompt/project onboarding guide** for adding a new `project_id`

---

## 19. Summary

To integrate Vehix chatbot with AI Hub in dev/test:
- call `POST /v1/chat`
- restore session with `GET /v1/users/{user_name}/sessions`
- always send `X-API-KEY`
- use `project_id = vehix`
- use `model_mode = lite` first
- keep `stream = false`
- persist returned `session_id` in Vehix

This is enough for a developer to connect Vehix chatbot to AI Hub immediately in dev/test.
