# GDPR data export + erasure (P2.6, 2026-06-10)

Implements GDPR Articles 15 (right of access) and 17 (right to
erasure), and the analogous rights under Vietnamese PDPA
(Nghị định 13/2023/NĐ-CP Art. 11).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/v1/admin/users/{user_id}/data-export` | Return all data for a user (Art. 15) |
| `POST` | `/v1/admin/users/{user_id}/gdpr-delete?grace_days=30` | Schedule a deletion (default 30-day grace) |
| `POST` | `/v1/admin/users/{user_id}/gdpr-cancel` | Cancel a pending deletion |
| `GET`  | `/v1/admin/users/{user_id}/gdpr-status` | Current GDPR state |
| `DELETE` | `/v1/admin/users/{user_id}?confirm=true` | Force-delete RIGHT NOW (irreversible) |

All endpoints require admin auth (existing `require_admin`
dependency).

## Flow

```
[Operator / user]                 [Scheduler (daily 03:13)]
        |                                       |
   POST /gdpr-delete?grace_days=30              |
        |                                       |
   SET gdpr_delete_scheduled_for = NOW() + 30d  |
        |                                       |
   (30 days pass)                               |
        |                                       |
   [optional: POST /gdpr-cancel before deadline] |
        |                                       |
        |     SELECT users WHERE scheduled_for < NOW() AND gdpr_deleted_at IS NULL
        |<--------------------------------------|
        |                                       |
   (scheduler calls hard_delete_user)            |
        |                                       |
   DELETE FROM <12 user-scoped tables>           |
   UPDATE api_keys SET enabled=0 WHERE owner=... |
   DELETE FROM users WHERE id = ?               |
        |                                       |
   (irreversible — user_id no longer exists)    |
```

## What gets deleted

| Table | Action |
|---|---|
| `memory_boundaries` | DELETE |
| `memory_episodes` | DELETE |
| `memory_items` | DELETE |
| `memory_consolidations` | DELETE |
| `pinned_memories` | DELETE |
| `summaries` | DELETE |
| `prediction_records` | DELETE |
| `fanpage_facts` | DELETE |
| `sessions` | DELETE (after messages) |
| `messages` | DELETE (via resolved session_ids) |
| `usage_events.user_id` | NULL — keeps `api_key_id` for billing |
| `failure_risk_events.user_id` | NULL — same |
| `api_keys.owner_user_id` | disable (`enabled=0`), do NOT delete (keeps usage history) |
| `users` | DELETE |

## Why NULL user_id on usage_events?

GDPR Art. 17(3)(e) lets us keep data "for the establishment,
exercise or defence of legal claims". Billing records (api_key_id
+ cost_usd + latency) are legitimate financial records; the
user_id is PII we erase, the rest is preserved.

## 30-day grace

Default 30 days. Admin can override via `?grace_days=N` (1-365).
The user (or admin) can cancel any time before the deadline via
`POST /gdpr-delete/cancel`.

After 30 days, the daily 03:13 sweep picks up the user and runs
the hard delete automatically. If the sweep fails (DB blip), the
next day's run retries. No data is lost in the gap — it's still
queued.

## Operator runbook

### User asks "where's my data?"

```bash
curl -H "X-API-KEY: $ADMIN" \
  http://localhost:8000/v1/admin/users/u_abc123/data-export \
  | jq . > export.json
```

Send the JSON to the user (or zip + signed URL if it's >10MB).

### User asks "delete my account"

```bash
# Schedule for 30 days from now
curl -H "X-API-KEY: $ADMIN" -X POST \
  "http://localhost:8000/v1/admin/users/u_abc123/gdpr-delete?grace_days=30"

# User changes their mind?
curl -H "X-API-KEY: $ADMIN" -X POST \
  http://localhost:8000/v1/admin/users/u_abc123/gdpr-cancel
```

### User wants immediate deletion (no grace)

```bash
# IRREVERSIBLE — must pass ?confirm=true
curl -H "X-API-KEY: $ADMIN" -X DELETE \
  "http://localhost:8000/v1/admin/users/u_abc123?confirm=true"
```

The response includes a per-table summary so you can verify the
delete landed.

## Schema migration

`users` table gets 3 new columns (idempotent migration):
- `gdpr_delete_requested_at TIMESTAMP` — when the request was made
- `gdpr_delete_scheduled_for TIMESTAMP` — when hard-delete will run
- `gdpr_deleted_at TIMESTAMP` — when the hard-delete actually fired

Existing rows have all 3 columns = NULL, which means "no pending
deletion, user is alive".

## Tests

`tests/unit/test_gdpr_service.py` (14 tests):
- request/cancel flow + idempotency
- hard_delete wipes messages + sessions
- data_export returns the full dict
- All 4 admin endpoints work
- Scheduler sweep job runs without error

## Reference

- GDPR Art. 15: https://gdpr-info.eu/art-15-gdpr/
- GDPR Art. 17: https://gdpr-info.eu/art-17-gdpr/
- Vietnamese PDPA (Nghị định 13/2023/NĐ-CP) Art. 11
