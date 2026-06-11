# Database SSL (P2.3, 2026-06-10)

AI Hub's Postgres connection must be encrypted. This runbook covers
how to enable SSL on the Postgres side and how to configure
`DATABASE_URL` on the AI Hub side.

## TL;DR

Append `?sslmode=require` to `DATABASE_URL` in `.env`:

```ini
DATABASE_URL=postgresql://aihub:aihub_pass@localhost:5432/ai_hub?sslmode=require
```

At startup, AI Hub logs the effective sslmode. If you see
`sslmode=disable`, your connection is unencrypted.

## Postgres side (Ubuntu / apt postgresql-16)

1. Generate a self-signed cert (dev only):
   ```bash
   sudo openssl req -new -x509 -days 365 -nodes \
     -out /var/lib/postgresql/16/main/server.crt \
     -keyout /var/lib/postgresql/16/main/server.key \
     -subj "/CN=localhost"
   sudo chown postgres:postgres /var/lib/postgresql/16/main/server.{crt,key}
   sudo chmod 600 /var/lib/postgresql/16/main/server.key
   ```

2. Edit `/etc/postgresql/16/main/postgresql.conf`:
   ```ini
   ssl = on
   ssl_cert_file = '/var/lib/postgresql/16/main/server.crt'
   ssl_key_file = '/var/lib/postgresql/16/main/server.key'
   ```

3. Restart:
   ```bash
   sudo systemctl restart postgresql
   ```

4. Verify:
   ```bash
   PGPASSWORD=aihub_pass psql "sslmode=require host=localhost user=aihub dbname=ai_hub" -c "SELECT 1;"
   ```
   Should return `1`.

## Production checklist

- [ ] `sslmode=verify-full` (not just `require` — verifies the cert)
- [ ] `sslrootcert=/path/to/ca.crt` pointing at your CA bundle
- [ ] Postgres `ssl = on` + certs signed by your internal CA
- [ ] `pg_hba.conf` has at least one `hostssl` rule (rejects non-SSL)
- [ ] Cert rotation runbook (`docs/security/secret-rotation.md` P2.4)

## Why `sslmode=require` for dev, `verify-full` for prod

| Mode | Encrypts? | Verifies cert? | Use case |
|---|---|---|---|
| `disable` | No | No | Never (warns) |
| `allow`  | No | No | Never (default if no param) |
| `prefer` | Tries | No | Avoid (silent fallback to plaintext) |
| `require` | **Yes** | **No** | Dev (self-signed cert) |
| `verify-ca` | Yes | CA only | Internal CA prod |
| `verify-full` | Yes | CA + hostname | Public-facing prod |

## Implementation notes

- The change is **app-side only** for the dev path: we just append
  `?sslmode=require` to the URL. psycopg 3 parses the query string
  and configures the SSL socket automatically. No code change to
  `_get_pool()` beyond logging the effective mode.
- `app/core/database.py::_get_effective_sslmode()` is the helper that
  parses the URL and emits the startup log.
- All scripts that read `DATABASE_URL` directly (migrations, seed
  scripts, backup) inherit the SSL setting automatically — no changes
  needed in `scripts/`.

## Tests

`tests/unit/test_db_ssl.py` (7 tests):
- Parser covers `require`, `verify-full`, missing param, empty URL, garbage URL
- Startup-log warning path is exercised
- The test conftest's `DATABASE_URL` env is asserted to include `sslmode=require`
  so all unit tests run encrypted

## Reference

- OWASP: https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Protection_Cheat_Sheet.html
- PostgreSQL SSL docs: https://www.postgresql.org/docs/16/ssl-tcp.html
- psycopg 3 SSL: https://www.psycopg.org/psycopg3/docs/advanced/async.html#ssl
