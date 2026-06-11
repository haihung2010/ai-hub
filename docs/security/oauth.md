# OAuth 2.1 Client Credentials grant (P2.1, 2026-06-10)

Foundation for replacing `X-API-KEY` with short-lived OAuth 2.1
bearer tokens. Reference: IETF draft-ietf-oauth-v2-1-15 (2026-03-02)
§4.2.

## What's in this PR

- HS256-signed JWT access tokens
- `POST /v1/oauth/token` endpoint with the Client Credentials
  grant (RFC 6749 §4.4 → OAuth 2.1 §4.2)
- `verify_oauth_token()` helper that the security middleware
  will use in the P2.1 follow-up
- Scopes: `chat`, `admin`, `a2a`, `tools` (default: `chat a2a tools`
  for non-admin keys, all four for admin keys)
- Tokens carry `sub` (api_key_id), `tenant_id`, and `scope` claims
- Default token lifetime: 1h. Floor 1 minute, ceiling 24 hours.
- The master API_KEY and per-tenant api_keys BOTH work as
  Client Credentials (the `client_id` is the api_key_id, the
  `client_secret` is the raw_key)

## What's NOT in this PR (deferred)

- Authorization Code + PKCE for browser flows (not needed for M2M)
- RS256 / JWKS endpoint (HS256 is fine for first-party M2M; switch
  to RS256 when you need third-party verifiers)
- Refresh tokens (Client Credentials doesn't need them)
- Token revocation list (tokens are short-lived; expiry IS the
  revocation)
- Migrating the security middleware to accept bearer tokens
  (the follow-up to this PR)

## Usage

```bash
# Exchange an API key for a bearer token
curl -X POST http://localhost:8000/v1/oauth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=ak_abc123" \
  -d "client_secret=ah_long_random_secret"

# Response
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "chat a2a tools"
}

# Use the bearer token on subsequent requests
curl -H "Authorization: Bearer eyJ..." http://localhost:8000/v1/chat
```

## Two ways to send credentials

The endpoint accepts the credentials in either form (RFC 6749 §2.3.1):

```bash
# Form params (simpler for M2M scripts)
curl -X POST http://localhost:8000/v1/oauth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=ak_..." \
  -d "client_secret=ah_..."

# HTTP Basic auth header (preferred for libraries that wrap OAuth)
curl -X POST http://localhost:8000/v1/oauth/token \
  -u "ak_...:ah_..." \
  -d "grant_type=client_credentials"
```

## Token contents (decoded JWT payload)

```json
{
  "iss": "ai-hub",
  "sub": "ak_abc123",
  "tenant_id": "cw_99",
  "scope": "chat a2a tools",
  "iat": 1781139631,
  "exp": 1781143231,
  "aud": "ai-hub-api"
}
```

The middleware (next PR) will read these claims and use
`tenant_id` to bind RLS context and `scope` to authorize the
specific route.

## Signing key

HS256 secret. Sourced from `OAUTH_JWT_SECRET` env var; falls
back to `API_KEY` if unset (NOT recommended for production — set
a 32+ char random value).

## Migration path (one release dual-validate)

When this becomes the default auth scheme:
1. Add `verify_oauth_token()` to the security middleware,
   AFTER the X-API-KEY check.
2. Bearer token takes precedence (faster path).
3. X-API-KEY is the fallback.
4. Log a `DeprecationWarning` per X-API-KEY request.
5. Remove X-API-KEY support one release later.

## Tests

`tests/unit/test_oauth.py` (16 tests):
- Issue / verify round-trip
- Tampered / expired / garbage tokens rejected
- Unknown scopes rejected, ttl clamped
- `authenticate_client` accepts valid + rejects bad secrets
- `/v1/oauth/token` accepts form params AND Basic auth
- Wrong grant type → 400 unsupported_grant_type
- Bad credentials → 401 invalid_client
- Non-admin asking for `admin` scope → 400 invalid_scope
- Round-trip: issued token has correct `tenant_id` and `sub`

## Reference

- OAuth 2.1 draft-15: https://datatracker.ietf.org/doc/draft-ietf-oauth-v2-1/
- RFC 6749 §4.4 (Client Credentials): https://datatracker.ietf.org/doc/html/rfc6749#section-4.4
- PyJWT docs: https://pyjwt.readthedocs.io/
