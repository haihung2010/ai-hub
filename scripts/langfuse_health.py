"""Langfuse local-stack health probe.

Calls:
  - GET ${LANGFUSE_HOST:-http://localhost:3000}/api/public/health
  - GET ${LANGFUSE_HOST:-http://localhost:3000}/api/public/otel/v1/traces
    (this endpoint accepts POST/OTLP only; a GET should return 405,
    which still confirms the route is wired and the server is up.)

Exits 0 if the public health endpoint returns 200, else 1.
"""

from __future__ import annotations

import os
import sys
import json
import urllib.error
import urllib.request


HEALTH_PATH = "/api/public/health"
OTLP_PATH = "/api/public/otel/v1/traces"
DEFAULT_HOST = "http://localhost:3000"


def _http(url: str, method: str = "GET", timeout: float = 5.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - best effort body read
            pass
        return exc.code, body
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    host = os.environ.get("LANGFUSE_HOST", DEFAULT_HOST).rstrip("/")
    health_url = f"{host}{HEALTH_PATH}"
    otlp_url = f"{host}{OTLP_PATH}"

    print(f"Langfuse host: {host}")
    print(f"Probing:       {health_url}")

    health_status, health_body = _http(health_url)
    if health_status == 200:
        print(f"  status:  200 OK")
        try:
            payload = json.loads(health_body)
            version = payload.get("version") or payload.get("release") or "unknown"
        except json.JSONDecodeError:
            version = "unknown"
        print(f"  version: {version}")
    else:
        print(f"  status:  {health_status} (FAIL)")
        if health_body:
            print(f"  body:    {health_body[:200]}")
        print("OTLP endpoint probe: skipped (health endpoint not healthy)")
        return 1

    print(f"Probing OTLP:   {otlp_url}  (GET, expect 405)")
    otlp_status, otlp_body = _http(otlp_url, method="GET")
    print(f"  status:  {otlp_status}")
    if otlp_status == 0:
        print(f"  body:    {otlp_body}")
        return 1
    if otlp_status == 405:
        print("  OK:      OTLP endpoint is wired (405 Method Not Allowed on GET is expected).")
    else:
        print(f"  note:    unexpected status {otlp_status}; endpoint is still reachable.")

    print("Langfuse local stack: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
