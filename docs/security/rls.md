# Row Level Security (P2.2, 2026-06-10)

AI Hub relies on app-level `WHERE tenant_id = %s` clauses to
isolate one tenant from another. If a future code change forgets
the clause, data leaks across tenants — silent, undetectable.

Postgres Row Level Security (RLS) is the second line of defense:
even if the WHERE clause is missing, Postgres itself filters
rows by `current_setting('app.current_tenant')`.

OWASP API1:2023 (BOLA — Broken Object Level Authorization) +
defense in depth at the DB layer.

## How it works

Every connection that wants to read tenant-scoped tables must
bind the GUC first:

```python
from app.core.database import get_db_connection

with get_db_connection(tenant_id="acme") as conn:
    rows = conn.execute("SELECT * FROM messages").fetchall()
    # Only returns rows where tenant_id = 'acme'.
```

The connection is a transaction; `SET LOCAL` releases the GUC at
COMMIT/ROLLBACK. Two concurrent requests on the same worker get
two different transactions with two different GUCs — no
cross-tenant bleed.

A connection WITHOUT a bound tenant (i.e. the call site didn't
pass `tenant_id=`) sees ZERO rows in any RLS-protected table.
This is the secure default — forgetting the tenant is treated as
"show nothing", not "show everything".

## Tables under RLS

`app.core.database.RLS_TABLES` lists 14 tables:

```
messages, memory_items, memory_episodes, memory_consolidations,
memory_boundaries, pinned_memories, summaries, sessions,
usage_events, failure_risk_events, prediction_records,
fanpage_facts, api_keys, skills
```

## What ISN'T RLS-protected

- `knowledge_cards`, `knowledge_card_chunks` — tenant+project
  scoped, but the access path is the knowledge retrieval service
  which always filters. Lower risk.
- `a2a_audit_log` — has `api_key_id` but no `tenant_id` column
  in some older rows. Audit log is by design append-only across
  tenants; not RLS-eligible.
- `ihi_rag_cases` — no user/tenant scope.

If you add a new tenant-scoped table:
1. Add it to `RLS_TABLES` in `app/core/database.py`
2. Restart — the migration creates the policy automatically
3. The new table's policy is `tenant_id = current_setting('app.current_tenant', true)`

## Gotchas

1. **Superusers bypass RLS.** Don't run AI Hub's app as a
   Postgres superuser in production — the policy becomes a no-op.
   The dev DB user (`aihub`) IS a superuser in our setup, which
   is why the RLS isolation tests skip in CI. Production must
   use a non-privileged user.

2. **Table owners bypass RLS** too, unless `FORCE ROW LEVEL
   SECURITY` is set on the table. Add that to the migration
   for any table whose app user is also the owner.

3. **SET LOCAL doesn't accept parameter binding** in Postgres.
   The code uses string interpolation with a strict whitelist
   (alphanumeric + `-_.`). Don't bypass that.

4. **Connection pooling**: SET LOCAL is per-transaction, so the
   GUC is bound fresh every time the connection is yielded. No
   leftover state from a previous request.

## Tests

`tests/unit/test_rls.py` (5 tests):
- `get_db_connection(tenant_id=...)` sets the GUC
- `get_db_connection()` (no tenant) leaves the GUC null
- `RLS_TABLES` is exported and contains the expected tables
- `pg_class.relrowsecurity` is true for all listed tables
  (skipped if RLS enablement failed at startup)
- Cross-tenant isolation works on `messages` (skipped if the DB
  user bypasses RLS, e.g. a superuser)

## Production checklist

- [ ] App's DB user is NOT a superuser
- [ ] App's DB user is NOT the owner of any RLS table
  (or every table has `FORCE ROW LEVEL SECURITY`)
- [ ] All query sites that read tenant-scoped data pass
  `tenant_id=` to `get_db_connection()` (grep for
  `get_db_connection()` to find call sites that don't)
- [ ] Admin / reporting queries that need to see across tenants
  use a separate non-RLS connection (e.g. a `with
  get_db_connection(tenant_id=None)` block, or a dedicated
  connection pool)

## Reference

- Postgres RLS docs: https://www.postgresql.org/docs/16/ddl-rowsecurity.html
- OWASP API1:2023 (BOLA): https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
