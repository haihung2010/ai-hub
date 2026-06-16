# Chat Test Viewer — Design

**Date:** 2026-06-16
**Status:** Approved
**Author:** Brainstorming session with user
**Related:** `static/chat.html` (aihub), `static/admin.html` (aihub)

---

## 1. Background & Motivation

Hiện tại trong ai-hub có 2 chỗ xem chat test data:

1. **`static/admin.html` → tab Tenants → chọn user → "Open Chat Viewer"** mở `static/chat.html` ở tab mới. Đây là drill-down cho 1 user.
2. **`static/admin.html` → tab Audit → dropdown chọn user** hiển thị 200 message inline.

**Vấn đề với workflow hiện tại:**

- Phải vào admin shell (685 dòng HTML + 2177 dòng JS) mới xem được. Mỗi lần test muốn monitor phải navigate nhiều bước.
- Audit tab yêu cầu **chọn user trước** rồi mới list message → không thấy được overview "ai vừa chat" mà phải đoán user_id.
- Không thấy system stats (RPM, latency p95, error rate) song song với user list.
- Muốn mở nhiều user đồng thời phải mở nhiều tab admin, mỗi tab navigate lại từ đầu.
- Admin UI đang phình to (admin.html + admin.js + admin.css ≈ 4300 dòng), thêm tính năng sẽ tăng coupling và bundle size.

**Giải pháp:** Tạo folder độc lập `/home/hung/chat-test-viewer/` chứa 1 trang top-level monitor (`index.html`) + 1 trang drill-down (`chat.html` copy từ aihub, sửa base URL + key để chạy độc lập). Trang này gọi aihub qua HTTP API + `X-API-KEY`, không bundle code aihub. Sau này user tích hợp lại nếu cần.

---

## 2. Architecture

```
chat-test-viewer/                    (git init riêng, không submodule)
├── README.md                        # Hướng dẫn config + chạy
├── index.html                       # Top-level monitor
├── chat.html                        # Drill-down 1 user (sửa từ aihub)
├── css/
│   ├── theme.css                    # Tokens (màu, glass, radius) — copy từ aihub admin.css
│   └── app.css                      # Layout 2-pane
├── js/
│   ├── api.js                       # Aihub client: getSystemStats, getRecentUsers, getUserMessages
│   ├── config.js                    # API base URL + key, localStorage helpers
│   ├── userList.js                  # Render + sort selector (client-side)
│   ├── systemStats.js               # Top stats panel
│   └── main.js                      # Wire-up, refresh button
├── tests/
│   ├── api.test.js                  # Mock fetch, assert URL/headers
│   ├── config.test.js               # localStorage round-trip
│   ├── userList.test.js             # Sort selector (4 modes)
│   └── e2e.spec.js                  # Playwright: load page, mock server, assert
├── package.json                     # vitest + @playwright/test
└── vitest.config.js
```

### Pages & URLs

| URL | Vai trò | Source |
|---|---|---|
| `index.html` (root) | Top-level monitor | New |
| `chat.html?user_id=&user_name=&api_url=&api_key=` | Drill-down 1 user | Copy từ `static/chat.html` của aihub, sửa API base + key handling |

### Layout `index.html`

```
┌─────────────────────────────────────────────────────────────────────┐
│  ⚡ CHAT TEST MONITOR                                  [↻ Refresh]  │
│  API: http://localhost:8000   [⚙ Config]                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──── SYSTEM STATS ──────────────────────────────────────────────┐ │
│  │  Active users (24h): 47    Total messages: 12,341              │ │
│  │  Avg latency: 1.4s        P95 latency: 4.2s                    │ │
│  │  Top model: gemma-12b     Errors (1h): 3                       │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌──── USERS (24) ─── Tenant: [● All ▾] ─────────────────────────┐  │
│  │ Sort: [● Newest ▾] [Oldest] [Name] [Request count]   🔍 filter │  │
│  │ ┌──────────────────────────────────────────────────────────┐   │  │
│  │ │ user_42   5 msgs   14:32   gemma-12b  1.2s avg     →     │   │  │
│  │ │ user_07   3 msgs   14:31   gemma-12b  0.9s avg     →     │   │  │
│  │ │ user_19   8 msgs   14:30   gemma-12b  2.1s avg     →     │   │  │
│  │ │ ...                                                      │   │  │
│  │ └──────────────────────────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Backend Integration

Tất cả dữ liệu lấy qua HTTP API có sẵn của aihub. Không cần thay đổi aihub backend.

| Endpoint | Dùng cho | Notes |
|---|---|---|
| `GET /v1/admin/stats` | System stats panel | Trả về `total_requests`, `success_requests`, `error_requests`, `avg_latency_ms`, `p50/p95/p99`, `by_model[]`, `month_cost_usd` |
| `GET /v1/admin/tenants` | Populate tenant dropdown | Trả về list `project_id` đang active |
| `GET /v1/admin/tenants/{project_id}/users` | User list (per tenant) | Trả về `id`, `name`, `message_count`, `session_count`, `last_message_at`, `is_active` |
| `GET /v1/admin/users/{user_id}/messages?truncate=0&limit=200` | Drill-down messages | Trả về `model`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `cost_usd` qua LATERAL JOIN với `usage_events` |

### CORS requirement

Mở `index.html` qua `file://` hoặc port khác → aihub CORS sẽ block. User phải thêm origin vào `ALLOWED_ORIGINS` trong aihub `.env`. README sẽ ghi rõ, ví dụ:
```
ALLOWED_ORIGINS=["http://localhost:8000","http://localhost:5500","null"]
```
(`null` cho phép mở qua `file://`)

---

## 4. Data Flow

### `index.html` load

```
1. Read config (api_url, api_key) from localStorage
   ├── missing → show config modal, block until filled
   └── present → continue
2. Fetch tenants list → /v1/admin/tenants
   ├── empty → show error
   └── present → populate tenant dropdown (default: first tenant)
3. Parallel fetch:
   ├── api.getSystemStats()    → /v1/admin/stats
   └── api.getRecentUsers()    → /v1/admin/tenants/{selected_tenant}/users
4. Render stats panel
5. Render user list (sort = "newest" mặc định)
```

### Sort selector change

```
1. User chọn sort mode (newest/oldest/name/request_count)
2. Re-sort user list CLIENT-SIDE trên data đã có
3. Re-render list
4. KHÔNG gọi lại API (tránh N+1 và giữ latency thấp)
```

### Tenant dropdown change

```
1. User chọn tenant khác
2. Re-fetch user list với project_id mới (giữ sort selection)
3. KHÔNG re-fetch system stats (stats là global, không theo tenant)
```

### Refresh button click

```
1. Re-fetch stats + tenants + user list cho tenant hiện tại
2. Giữ sort + tenant selection hiện tại
3. Re-render
4. Hiển thị "Last refreshed: HH:MM:SS"
```

### Click user row

```
1. window.open('chat.html?user_id=...&user_name=...&api_url=...&api_key=...', '_blank')
2. Truyền API URL + key qua URL params (chat.html cũng đọc localStorage làm fallback)
3. KHÔNG embed trong cùng tab → user có thể mở song song nhiều user
```

### `chat.html` load

```
1. Read params: user_id (required), user_name, api_url, api_key
   ├── missing api_url/api_key → fall back to localStorage
   └── still missing → show config modal
2. api.getUserMessages(user_id, {truncate: 0, limit: 200})
3. Render messages theo user-assistant pair (giống aihub chat.html):
   ┌──────────────────────────────────┐
   │ user: "Hi, I'm new here"         │ 14:32
   │   ↓
   │ assistant: "Welcome! ..."         │ 14:32
   │   meta: gemma-12b • 1.2s • 142+86 tok • $0.0001
   └──────────────────────────────────┘
```

---

## 5. Error Handling

| Tình huống | UI response |
|---|---|
| API key rỗng hoặc 401 | Toast "Auth failed" + mở config modal |
| API trả 5xx | Toast "Server error: <status>" + retry button |
| Empty user list | Empty state "No recent activity (24h)" với icon |
| Empty messages | Empty state "User hasn't chatted yet in this project" |
| CORS blocked | Toast hướng dẫn: "Add this origin to aihub ALLOWED_ORIGINS" |
| Network offline | Toast "Cannot reach API at <url>" + check button |
| Config invalid (URL malformed) | Inline validation trong config modal, disable Save button |

### Config modal (khi mở app lần đầu hoặc bấm ⚙)

```
┌── CONFIG ──────────────────────┐
│ API URL: [http://localhost:8000]│
│ API Key: [••••••••••]           │
│ [Cancel]              [Save]   │
└─────────────────────────────────┘
```

- Lưu localStorage keys: `aihub_api_url`, `aihub_api_key`
- API key KHÔNG log ra console
- Validate URL: bắt đầu bằng `http://` hoặc `https://`

---

## 6. Testing Strategy

| Layer | Tool | Coverage |
|---|---|---|
| Unit | Vitest | `api.js` (mock fetch, assert URL/headers/auth), `config.js` (localStorage round-trip), `userList.js` (4 sort modes edge cases) |
| Integration | Vitest + jsdom | Full `index.html` mount, mock fetch, assert DOM updates |
| E2E | Playwright | Load `index.html` qua local server, mock aihub API bằng route interception, click refresh, click user row → assert new tab URL contains `chat.html?user_id=...` |
| Manual | Browser | Point vào aihub thật (port 8000), chạy 1 quick load test 5 users, mở viewer, verify user list update sau refresh |

**Coverage target: 80%** (theo repo rule trong `common/testing.md`).

---

## 7. Out of Scope (YAGNI)

- Auto-poll / SSE / WebSocket (user chọn manual refresh ở brainstorming)
- Ghi hoặc export chat history ra file
- Real-time multi-user collaboration (multiple users viewing same page)
- Dark/light theme toggle (chỉ dark, match aihub theme)
- Modify aihub backend (no schema change, no new endpoint)
- Authentication beyond `X-API-KEY` (no OAuth, no session)
- Tích hợp vào admin.html (giữ standalone; user tích hợp sau nếu muốn)

---

## 8. Open Questions

Không có — đã chốt trong brainstorming:

- Approach A (standalone folder, reuse chat.html as drill-down)
- 2 trang riêng biệt (`index.html` + `chat.html`)
- Manual refresh button
- Sort mặc định: Newest
- 4 sort modes: Newest, Oldest, Name, Request count
- CORS: user tự thêm origin vào aihub `.env`
