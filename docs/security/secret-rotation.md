# Secret rotation policy (P2.4, 2026-06-10)

AI Hub uses four kinds of secrets. Each has its own rotation cadence
based on the blast radius if leaked.

## Cadence

| Secret | Where it lives | Cadence | How to rotate |
|---|---|---|---|
| Master `API_KEY` | `api_keys` table | **90 days** | `POST /v1/admin/keys/{id}/rotate` |
| Per-tenant API keys | `api_keys` table | **90 days** | Same as above |
| `MINIMAX_API_KEY` | env var | **75 days** (per MiniMax policy) | Manually mint a new key in MiniMax console, update env, restart |
| `CHATWOOT_WEBHOOK_SECRET` | env var | **180 days** | Generate new HMAC, update Chatwoot side + env, restart |
| DB password | `DATABASE_URL` (env) | **180 days** | `ALTER USER aihub PASSWORD '...'` on Postgres, update `DATABASE_URL`, restart |

## Why these numbers

- **API keys** (90d) — the most-exposed surface (any client with the
  key can call `/v1/chat`). Industry standard (Stripe, GitHub) is
  90-180 days; we picked 90 to be conservative.
- **Cloud provider keys** (75d) — MiniMax's own policy is 60-90d;
  we sit at 75d to give a 2-week buffer.
- **Webhook HMAC** (180d) — only used to verify Chatwoot deliveries.
  Compromise lets the attacker forge webhooks, but they can already
  reach our `/v1/integrations/chatwoot/*` endpoints from Chatwoot
  itself, so the blast radius is small.
- **DB password** (180d) — internal network, no external attack
  surface. The risk is internal-credential rotation hygiene, not
  remote compromise.

## Daily reminder job

AI Hub's APScheduler runs `_rotation_reminder_job` at **09:07 local
time** every day. It:

1. Queries `api_keys` for rows whose `last_rotated_at` is older
   than 90 days.
2. Logs a `WARNING` per stale key with id, name, tenant, days
   since rotation.
3. Emits a periodic reminder for the env-managed secrets
   (MiniMax, webhook HMAC, DB password) — these don't have
   per-row timestamps, so the reminder is "verify out-of-band".

The job is independent of `adaptive_routing_enabled` so it runs
even when other background tasks are off.

## Operator runbook

### Rotating a single API key

```bash
# 1. List stale keys
curl -H "X-API-KEY: $ADMIN_KEY" \
  http://localhost:8000/v1/admin/keys/rotation-status?rotation_days=90

# 2. Rotate one of them
NEW=$(curl -H "X-API-KEY: $ADMIN_KEY" -X POST \
  http://localhost:8000/v1/admin/keys/ak_abc123/rotate | jq -r .new_raw_key)

# 3. Distribute $NEW to the client (out of band — Slack, email,
#    secret manager). The old key stops working immediately.

# 4. Verify
curl -H "X-API-KEY: $NEW" http://localhost:8000/health
```

### Rotating the master `API_KEY`

The master key is just a row in `api_keys` with `name='master'`.
Use the same endpoint:

```bash
MASTER_ID=$(curl -H "X-API-KEY: $OLD_MASTER" \
  http://localhost:8000/v1/admin/keys/rotation-status?rotation_days=90 \
  | jq -r '.stale_keys[] | select(.name=="master") | .id')
NEW_MASTER=$(curl -H "X-API-KEY: $OLD_MASTER" -X POST \
  "http://localhost:8000/v1/admin/keys/$MASTER_ID/rotate" \
  | jq -r .new_raw_key)
echo "new master: $NEW_MASTER"  # store NOW, never shown again
```

### Rotating env-managed secrets

1. Mint the new value (MiniMax console / `openssl rand -hex 32`
   for HMAC / `ALTER USER ... PASSWORD` for DB).
2. Update `.env` (or your secrets manager).
3. Restart AI Hub: `./start.sh` or `docker compose restart app`.
4. Update the consumer side (Chatwoot webhook config, MiniMax
   model allowlist, Postgres `pg_hba.conf`).
5. Test end-to-end.

## Tests

`tests/unit/test_api_key_rotation.py` (11 tests) covers:
- `ApiKeyRecord` exposes `last_rotated_at` + `created_at`
- `rotate_key()` mints a new raw_key, invalidates the old
- `rotate_key()` updates the `last_rotated_at` timestamp
- `rotate_key()` returns `None` for unknown ids
- `get_rotation_status()` finds ancient keys
- Admin endpoints (`/v1/admin/keys/rotation-status`,
  `POST /v1/admin/keys/{id}/rotate`) work end-to-end
- The daily reminder job is importable and runs without error

## Schema migration

`last_rotated_at TIMESTAMP` column added to `api_keys`. Idempotent:
the migration is guarded by `_column_exists()`. Existing rows
get backfilled with `last_rotated_at = created_at` so the rotation
clock starts at row creation, not at migration time.

## Reference

- NIST SP 800-57 Part 1 Rev. 5: Recommendation for Key Management
- OWASP Secrets Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
