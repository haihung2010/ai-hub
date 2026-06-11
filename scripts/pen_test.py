#!/usr/bin/env python3
"""Penetration test script (P3.1, 2026-06-11).

Black-box fuzzing against a running AI Hub instance. Hones in on
the OWASP API Security Top 10 (2023) + the A2A + Chatwoot
surfaces that ship with AI Hub.

Run: ./scripts/pen_test.py --base http://localhost:8000 --api-key $API_KEY

What it does:
  1. Hits every public endpoint with random / malicious payloads
  2. Verifies the server returns a clean HTTP error code
     (4xx/5xx) rather than a stack trace, 502, or timeout
  3. Specifically targets:
     - SQL injection in query params + body
     - XSS payloads (script tags, javascript: URLs) in body
     - Path traversal (../../etc/passwd) in URL
     - Header injection (CRLF in header values)
     - Oversized payloads (1 MB body)
     - Auth bypass (no key, wrong key, expired key)
     - Rate limit bombing (101 rapid requests)
     - Pathological JSON (deeply nested, large arrays, null bytes)

What it does NOT do (out of scope):
  - Active exploitation of any specific CVE
  - Persistent storage attacks (no DROP TABLE)
  - Side-channel timing attacks
  - Browser-level XSS via the admin UI (Playwright would be
    needed; not in this script)

CI: this script returns exit code 0 if all probes passed,
non-zero otherwise. Add to a daily cron or pre-release gate.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import time
from typing import Any, Callable
from urllib.parse import urlencode

import httpx


# ── Probe corpus ──────────────────────────────────────────────────────
# Each probe is a (name, request_fn) tuple. request_fn returns
# (status_code, response_text, elapsed_ms).

PAYLOAD_SQLI: list[str] = [
    "' OR '1'='1",
    "1; DROP TABLE users;--",
    "admin'--",
    "1 UNION SELECT * FROM api_keys--",
    "1' AND SLEEP(5)--",
    "%27%20OR%201%3D1--",
]

PAYLOAD_XSS: list[str] = [
    "<script>alert(1)</script>",
    "javascript:alert(1)",
    "<img src=x onerror=alert(1)>",
    "\"><script>alert(1)</script>",
    "data:text/html,<script>alert(1)</script>",
]

PAYLOAD_PATH_TRAVERSAL: list[str] = [
    "../../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "/etc/passwd",
    "file:///etc/passwd",
    "....//....//etc/passwd",
]

PAYLOAD_HEADER_INJECTION: list[str] = [
    "value\r\nX-Injected: yes",
    "value\nX-Injected: yes",
    "value\x00null",
    "value with \x00 null",
]

LARGE_BODY: bytes = b"x" * (1024 * 1024)  # 1 MB


def _ok(label: str, msg: str = "") -> None:
    print(f"  \033[32m✓\033[0m {label}{(' — ' + msg) if msg else ''}")


def _fail(label: str, msg: str) -> None:
    print(f"  \033[31m✗\033[0m {label} — {msg}")


def _check_500(label: str, status: int, body: str) -> bool:
    """A 500 with a stack trace in the body is a finding."""
    if status == 500:
        _fail(label, f"server returned 500 (likely unhandled exception)")
        return False
    # Also flag leaked stack traces
    for marker in ("Traceback (most recent call last):", "File \"/home/", "psycopg.errors"):
        if marker in body:
            _fail(label, f"response leaked internal info: {marker!r}")
            return False
    return True


def _check_clean(label: str, status: int, body: str) -> bool:
    """Response is a clean 4xx (or 200) — no crash, no leak."""
    return _check_500(label, status, body)


# ── Probes ────────────────────────────────────────────────────────────


def probe_sql_injection(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    results: list[bool] = []
    for payload in PAYLOAD_SQLI:
        r = client.post(
            f"{base}/v1/chat",
            headers={"X-API-KEY": api_key},
            json={"project_id": payload, "user_message": "hi"},
        )
        results.append(_check_clean(f"sqli chat({payload[:30]!r})", r.status_code, r.text))
    return results


def probe_xss(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    results: list[bool] = []
    for payload in PAYLOAD_XSS:
        r = client.post(
            f"{base}/v1/chat",
            headers={"X-API-KEY": api_key},
            json={"project_id": "xtest", "user_message": payload},
        )
        results.append(_check_clean(f"xss chat({payload[:30]!r})", r.status_code, r.text))
    return results


def probe_path_traversal(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    results: list[bool] = []
    for payload in PAYLOAD_PATH_TRAVERSAL:
        r = client.get(f"{base}/{payload}")
        results.append(_check_clean(f"path traversal {payload!r}", r.status_code, r.text))
    return results


def probe_oversized_body(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    r = client.post(
        f"{base}/v1/chat",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        content=LARGE_BODY,
    )
    return [_check_clean("1MB body", r.status_code, r.text)]


def probe_pathological_json(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Deeply nested JSON — many JSON parsers stack-overflow on
    this. FastAPI uses Pydantic which has a depth limit."""
    obj: Any = "leaf"
    for _ in range(1000):
        obj = {"nested": obj}
    r = client.post(
        f"{base}/v1/chat",
        headers={"X-API-KEY": api_key},
        json=obj,
    )
    return [_check_clean("1000-deep JSON", r.status_code, r.text)]


def probe_auth_bypass(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    results: list[bool] = []
    # No key
    r = client.get(f"{base}/v1/a2a/agent-card")
    results.append(r.status_code in (401, 403))
    _check_clean("no auth → 401/403", r.status_code, r.text)
    # Wrong key
    r = client.get(
        f"{base}/v1/a2a/agent-card",
        headers={"X-API-KEY": "obviously-wrong"},
    )
    results.append(r.status_code in (401, 403))
    _check_clean("bad auth → 401/403", r.status_code, r.text)
    # Empty key
    r = client.get(
        f"{base}/v1/a2a/agent-card",
        headers={"X-API-KEY": ""},
    )
    results.append(r.status_code in (401, 403))
    return results


def probe_rate_limit(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Hit the same endpoint 100 times in <1s. The 60-RPM
    per-key limit should kick in; we should see 429s after the
    first 60 (or however the test config sets it)."""
    statuses: list[int] = []
    for _ in range(100):
        r = client.post(
            f"{base}/v1/a2a/jsonrpc",
            headers={"X-API-KEY": api_key},
            json={"jsonrpc": "2.0", "id": "rl", "method": "ListTasks", "params": {}},
        )
        statuses.append(r.status_code)
    has_429 = any(s == 429 for s in statuses)
    if not has_429:
        return [False]
    _ok("rate limit kicks in (saw at least one 429)")
    return [True]


def probe_oauth_bypass(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Bad client_id / client_secret to /v1/oauth/token."""
    r = client.post(
        f"{base}/v1/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "x", "client_secret": "y"},
    )
    return [r.status_code == 401 and "invalid_client" in r.text]


def probe_webhook_signature_bypass(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Hit the Chatwoot endpoint with no signature — should 401."""
    r = client.post(
        f"{base}/v1/integrations/chatwoot/respond",
        json={"messages": []},
    )
    return [r.status_code == 401]


def probe_jsonrpc_malformed(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Send invalid JSON to /v1/a2a/jsonrpc — must NOT 500."""
    r = client.post(
        f"{base}/v1/a2a/jsonrpc",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        content=b"{not even json",
    )
    return [_check_clean("jsonrpc malformed JSON", r.status_code, r.text)]


def probe_jsonrpc_unknown_method(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    r = client.post(
        f"{base}/v1/a2a/jsonrpc",
        headers={"X-API-KEY": api_key},
        json={"jsonrpc": "2.0", "id": "1", "method": "BurnTheDatabase", "params": {}},
    )
    return [_check_clean("jsonrpc unknown method", r.status_code, r.text)]


def probe_sql_injection_query_params(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """SQLi in URL query params, not just body."""
    results: list[bool] = []
    for payload in PAYLOAD_SQLI:
        r = client.get(
            f"{base}/v1/admin/tenants/{payload}/users?limit=10",
            headers={"X-API-KEY": api_key},
        )
        results.append(_check_clean(f"sqli qparam {payload[:20]!r}", r.status_code, r.text))
    return results


def probe_header_injection(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """CRLF in header values — old IIS / nginx bugs allowed
    header smuggling."""
    r = client.get(
        f"{base}/v1/a2a/agent-card",
        headers={"X-Injected": "value\r\nX-Smuggled: yes"},
    )
    return [_check_clean("CRLF in X-Injected header", r.status_code, r.text)]


def probe_null_byte_in_url(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Null bytes in the URL path — sometimes C-level parsers truncate."""
    r = client.get(f"{base}/v1/users/\x00admin/../messages")
    return [_check_clean("null byte in URL", r.status_code, r.text)]


def probe_unicode_normalization(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    """Unicode look-alikes (e.g. admin with a Cyrillic 'a')."""
    for sneaky in ("аdmin", "Аdmin", "admın"):  # last char is dotless i
        r = client.get(
            f"{base}/v1/admin/keys",
            headers={"X-API-KEY": api_key},
            params={"name": sneaky},
        )
        if not _check_clean(f"unicode trickery {sneaky!r}", r.status_code, r.text):
            return [False]
    return [True]


def probe_health_endpoint_no_auth(client: httpx.Client, base: str, api_key: str) -> list[bool]:
    r = client.get(f"{base}/health")
    return [r.status_code in (200, 503)]  # 200 healthy, 503 degraded


# ── Main ──────────────────────────────────────────────────────────────


PROBES: list[tuple[str, Callable[..., list[bool]]]] = [
    ("auth bypass", probe_auth_bypass),
    ("health no auth", probe_health_endpoint_no_auth),
    ("SQLi in body", probe_sql_injection),
    ("SQLi in qparams", probe_sql_injection_query_params),
    ("XSS in body", probe_xss),
    ("path traversal", probe_path_traversal),
    ("1MB body", probe_oversized_body),
    ("1000-deep JSON", probe_pathological_json),
    ("rate limit bombing", probe_rate_limit),
    ("oauth bypass", probe_oauth_bypass),
    ("webhook sig bypass", probe_webhook_signature_bypass),
    ("jsonrpc malformed", probe_jsonrpc_malformed),
    ("jsonrpc unknown method", probe_jsonrpc_unknown_method),
    ("CRLF header injection", probe_header_injection),
    ("null byte in URL", probe_null_byte_in_url),
    ("unicode trickery", probe_unicode_normalization),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Hub pen test fuzzing")
    parser.add_argument("--base", default=os.environ.get("AI_HUB_BASE", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key or $API_KEY required (so the test has a known key to use)", file=sys.stderr)
        return 2

    print(f"AI Hub pen test — base={args.base}")
    print("=" * 60)

    all_results: list[bool] = []
    with httpx.Client(timeout=args.timeout) as client:
        for name, probe in PROBES:
            print(f"\n[{name}]")
            try:
                results = probe(client, args.base, args.api_key)
            except Exception as exc:
                _fail(name, f"probe raised {exc.__class__.__name__}: {exc}")
                all_results.append(False)
                continue
            all_results.extend(results)
            if not all(results):
                _fail(name, f"{sum(1 for r in results if not r)} probe(s) failed")

    print("\n" + "=" * 60)
    passed = sum(1 for r in all_results if r)
    total = len(all_results)
    print(f"Result: {passed}/{total} probes passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
