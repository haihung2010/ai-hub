# AI Hub Generic Project Integration Guide

This guide explains how any project should integrate with AI Hub in **dev/test** first, then adapt the same contract for staging or production.

---

## 1. Integration goal

AI Hub is the central chat backend for project-specific assistants.

A client project should use AI Hub for:
- chatbot question/answer flows
- session continuity and resume
- optional Lite-mode image input
- optional web search for current-information queries
- centralized prompt routing, memory, and model selection

The target integration is:
- your frontend or backend sends chat requests to AI Hub
- AI Hub handles authentication, session state, prompt loading, memory retrieval, and model calls
- your application renders the returned answer in its own UI

---

## 2. Base URLs by environment

### Local dev
- AI Hub UI/API: `http://localhost:8000`

### Public/shared endpoint
- AI Hub public endpoint: `https://api-aiserver.htechlabsvn.com/`

For initial integration, start with:
- `http://localhost:8000`

---

## 3. Required authentication

All protected API calls must include:

```http
X-API-KEY: <your-api-key>
Content-Type: application/json
```

### Notes
- API key is required for `POST /v1/chat`
- API key is required for `GET /v1/users/{user_name}/sessions`
- Missing or invalid key returns `401`
- Default rate limit is `5` requests/minute per API key

---

## 4. Main API endpoints

### 4.1 Chat endpoint

**Endpoint**

```http
POST /v1/chat
```

**Purpose**
- send a user message to AI Hub
- optionally continue an existing session
- optionally attach images in Lite mode
- receive the assistant reply

### 4.2 Session resume endpoint

**Endpoint**

```http
GET /v1/users/{user_name}/sessions?project_id=<project>&tenant_id=<tenant>
```

**Purpose**
- list resumable sessions for a known user
- restore the latest conversation in your chatbot UI

---

## 5. Chat request contract

AI Hub request schema is defined in `app/models/chat.py`.

### Request body

```json
{
  "project_id": "your-project",
  "tenant_id": "dev",
  "user_name": "alice",
  "user_message": "What are the open support tickets?",
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
| `project_id` | yes | string | Project identifier used by AI Hub to load the correct prompt/profile. |
| `tenant_id` | yes | string | Tenant or environment scope such as `dev`, `test`, `uat`, or a real tenant ID. |
| `user_name` | no | string | Stable user identifier for session restore and user-scoped memory. |
| `user_message` | yes | string | Current user prompt. |
| `images` | no | string[] | Base64 images. Only use when `model_mode = "lite"`. |
| `history` | no | Message[] | Explicit prior messages. Usually keep `[]` and rely on `session_id`. |
| `session_id` | no | string | Existing session to continue. If omitted, AI Hub creates a new one. |
| `stream` | no | boolean | Must currently be `false`. Streaming is not implemented yet. |
| `provider` | no | string | Optional provider override. Usually keep `null`. |
| `model_mode` | no | `normal` or `lite` | Select response profile. Lite is faster and supports images. |
| `enable_search` | no | boolean | Enable web search context for current-information queries. |

---

## 6. Chat response contract

### Example response

```json
{
  "project_id": "your-project",
  "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
  "model": "gemma4:e4b",
  "provider": "local",
  "content": "There are 4 open support tickets assigned to Operations.",
  "user_id": "0b0df11d-3e58-4df3-8f53-46a5a32d1c9f"
}
```

### Field meanings

| Field | Type | Description |
|---|---:|---|
| `project_id` | string | The resolved project ID |
| `session_id` | string | Persist this to continue the same chat later |
| `model` | string | Actual model used |
| `provider` | string | Usually `local` |
| `content` | string | Assistant response to display in your UI |
| `user_id` | string/null | Internal AI Hub user identifier |

---

## 7. Session resume API

### Request

```http
GET /v1/users/alice/sessions?project_id=your-project&tenant_id=dev
X-API-KEY: dev-api-key
```

### Example response

```json
[
  {
    "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
    "created_at": "2026-04-26 10:30:00",
    "last_message_preview": "There are 4 open support tickets assigned to Operations."
  }
]
```

### Recommended client behavior
- when user opens chatbot:
  1. load saved `user_name`
  2. call the session resume endpoint
  3. if sessions exist, take the newest one
  4. store `session_id`
  5. continue chat with that session ID
- if no sessions exist:
  - create a fresh session by calling `/v1/chat` without `session_id`

---

## 8. Recommended integration flow

```text
[User opens project chatbot]
        |
        +--> load saved user_name / apiKey / sessionId
        |
        +--> if user_name exists:
        |      GET /v1/users/{user_name}/sessions?project_id=<project>&tenant_id=<tenant>
        |      -> restore latest session_id if available
        |
        +--> user sends message
               POST /v1/chat
               {
                 project_id: "your-project",
                 tenant_id: "dev",
                 user_name,
                 user_message,
                 session_id,
                 model_mode: "lite",
                 enable_search: false
               }
        |
        +--> AI Hub returns ChatResponse
        |
        +--> client stores returned session_id
        |
        +--> client renders content
```

---

## 9. Recommended defaults for new project integrations

Use these settings first:

```json
{
  "project_id": "your-project",
  "tenant_id": "dev",
  "model_mode": "lite",
  "enable_search": false,
  "stream": false
}
```

### Why
- `lite` is faster in dev/test and supports images
- `enable_search: false` keeps responses focused on your project context first
- `stream: false` is required because streaming is not implemented yet

---

## 10. cURL examples

### 10.1 Start a new chat

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-API-KEY: dev-api-key' \
  -d '{
    "project_id": "your-project",
    "tenant_id": "dev",
    "user_name": "alice",
    "user_message": "Summarize today''s unresolved incidents",
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
    "project_id": "your-project",
    "tenant_id": "dev",
    "user_name": "alice",
    "session_id": "7f9d2d65-f3d1-4c27-9679-6d3b1c9d5a2b",
    "user_message": "Which incident is the oldest?",
    "model_mode": "lite",
    "enable_search": false,
    "stream": false
  }'
```

### 10.3 Get latest sessions for a user

```bash
curl 'http://localhost:8000/v1/users/alice/sessions?project_id=your-project&tenant_id=dev' \
  -H 'X-API-KEY: dev-api-key'
```

---

## 11. JavaScript fetch example

```ts
const AI_HUB_URL = 'http://localhost:8000';
const API_KEY = 'dev-api-key';

async function sendProjectChat({
  projectId,
  tenantId,
  userName,
  sessionId,
  userMessage,
}: {
  projectId: string;
  tenantId: string;
  userName: string;
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
      project_id: projectId,
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
async function restoreProjectSession(
  projectId: string,
  userName: string,
  tenantId: string,
) {
  const response = await fetch(
    `http://localhost:8000/v1/users/${encodeURIComponent(userName)}/sessions?project_id=${encodeURIComponent(projectId)}&tenant_id=${encodeURIComponent(tenantId)}`,
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

Your project may send images only when:
- `model_mode = "lite"`
- images are passed as Base64 strings in `images`

### Example

```json
{
  "project_id": "your-project",
  "tenant_id": "dev",
  "user_name": "alice",
  "user_message": "Read the text in this image",
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

| Code | Meaning | Recommended client behavior |
|---|---|---|
| `200` | Success | Render assistant response |
| `401` | Invalid/missing API key | Prompt for API key configuration |
| `429` | Rate limit exceeded | Retry later and show throttle message |
| `501` | Streaming not implemented | Force `stream=false` |
| `502` | Upstream/model error | Show temporary failure and allow retry |
| `503` | Ollama unavailable | Show service unavailable message |
| `504` | Upstream timeout | Show timeout and allow retry |

### Example UI-safe fallback

```ts
try {
  const result = await sendProjectChat(...);
  renderAssistantMessage(result.content);
} catch (error) {
  renderAssistantMessage('AI Hub is temporarily unavailable. Please try again.');
}
```

---

## 15. Onboarding a new project_id

Before integrating a new project, align these values:
- choose a stable `project_id`
- choose a tenant strategy (`dev`, `test`, `uat`, actual tenant IDs)
- decide whether the project needs `lite`, `normal`, or both
- decide whether browser clients call AI Hub directly or through a project backend proxy
- prepare a project-specific system prompt in AI Hub if custom behavior is needed

### Recommended minimum project profile

```json
{
  "project_id": "your-project",
  "tenant_id": "dev",
  "default_model_mode": "lite",
  "enable_search": false,
  "stream": false
}
```

---

## 16. Recommended dev/test checklist

### AI Hub side
- [ ] AI Hub is running on `http://localhost:8000`
- [ ] Ollama is available
- [ ] API key is known by the integrating team
- [ ] `project_id` is defined consistently
- [ ] CORS allows the calling origin if frontend calls AI Hub directly

### Client project side
- [ ] Save `user_name`
- [ ] Save returned `session_id`
- [ ] Always send `X-API-KEY`
- [ ] Default to `model_mode=lite` for dev/test
- [ ] Keep `stream=false`
- [ ] Implement resume flow with `/v1/users/{user_name}/sessions`
- [ ] Handle `401`, `429`, `502`, `503`, `504` gracefully

---

## 17. Suggested integration architecture

The cleanest default path is:

```text
Project Web UI / Mobile App
   -> Project backend proxy (optional)
   -> AI Hub /v1/chat
   -> AI Hub handles prompt + memory + model
   -> Project renders answer
```

### Recommended practice
- keep AI Hub as the chat orchestration layer
- keep project business-data adapters in the project domain or project-specific prompt/tooling
- store only lightweight chat state in the client: `user_name`, `session_id`, and API key only if your dev tooling needs it

---

## 18. Summary

For any project integrating with AI Hub in dev/test:
- call `POST /v1/chat`
- restore sessions with `GET /v1/users/{user_name}/sessions`
- always send `X-API-KEY`
- keep `stream = false`
- default to `model_mode = lite`
- persist returned `session_id`
- define a stable `project_id` and tenant strategy early

This is the minimum contract needed to connect a new project chatbot to AI Hub consistently.
