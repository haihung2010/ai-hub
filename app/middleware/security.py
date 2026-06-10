"""API key auth, host allow-listing, rate limiting, and security audit logging."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.services.api_key_service import ApiKeyRecord, ApiKeyService

logger = logging.getLogger("app.security")
API_KEY_HEADER = "X-API-KEY"
ALWAYS_PUBLIC_PATHS = {"/", "/webhooks/facebook"}
DOCS_PATHS = {"/docs", "/openapi.json", "/redoc"}
HEALTH_PATHS = {"/health"}
PWA_ROOT_FILES = {"/manifest.json", "/favicon.ico"}
PWA_ROOT_SUFFIXES = (".png", ".webmanifest", ".svg", ".woff", ".woff2")
RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass
class RateBucket:
    timestamps: deque[float] = field(default_factory=deque)
    lock: Lock = field(default_factory=Lock)


class InMemoryRateLimiter:
    def __init__(self, limit: int, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._buckets: dict[str, RateBucket] = {}
        self._lock = Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        bucket = self._get_bucket(key)
        with bucket.lock:
            while bucket.timestamps and current_time - bucket.timestamps[0] >= self._window_seconds:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= self._limit:
                return False
            bucket.timestamps.append(current_time)
            return True

    def _get_bucket(self, key: str) -> RateBucket:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = RateBucket()
                self._buckets[key] = bucket
            return bucket


@dataclass
class FailureState:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    lock: Lock = field(default_factory=Lock)


class AuthFailureTracker:
    """Small fail2ban-style in-process block list for bad API-key scans."""

    def __init__(self, limit: int, block_seconds: int, window_seconds: int = 300) -> None:
        self._limit = limit
        self._block_seconds = block_seconds
        self._window_seconds = window_seconds
        self._states: dict[str, FailureState] = {}
        self._lock = Lock()

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        state = self._get_state(key)
        with state.lock:
            return current_time < state.blocked_until

    def record_failure(self, key: str, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        state = self._get_state(key)
        with state.lock:
            while state.failures and current_time - state.failures[0] >= self._window_seconds:
                state.failures.popleft()
            state.failures.append(current_time)
            if len(state.failures) >= self._limit:
                state.blocked_until = current_time + self._block_seconds
        self._maybe_evict(current_time)

    def _maybe_evict(self, now: float) -> None:
        with self._lock:
            size = len(self._states)
            if size > 50000:
                self._states.clear()
                return
            if size <= 10000:
                return
            cutoff = now - self._window_seconds
            stale = [k for k, s in self._states.items() if not s.failures or s.failures[-1] < cutoff]
            for k in stale:
                self._states.pop(k, None)

    def reset(self, key: str) -> None:
        state = self._get_state(key)
        with state.lock:
            state.failures.clear()
            state.blocked_until = 0.0

    def _get_state(self, key: str) -> FailureState:
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = FailureState()
                self._states[key] = state
            return state


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets."""

    def __init__(self, limit: int, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> None:
        import redis as redis_lib
        from app.core.config import get_settings
        self._limit = limit
        self._window_seconds = window_seconds
        self._r = redis_lib.from_url(get_settings().redis_url, decode_responses=True)

    def allow(self, key: str, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        redis_key = f"rl:{key}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(redis_key, 0, current_time - self._window_seconds)
        pipe.zadd(redis_key, {str(uuid4()): current_time})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, self._window_seconds + 1)
        _, _, count, _ = pipe.execute()
        return count <= self._limit


class RedisFailureTracker:
    """Fail2ban-style block list backed by Redis."""

    def __init__(self, limit: int, block_seconds: int, window_seconds: int = 300) -> None:
        import redis as redis_lib
        from app.core.config import get_settings
        self._limit = limit
        self._block_seconds = block_seconds
        self._window_seconds = window_seconds
        self._r = redis_lib.from_url(get_settings().redis_url, decode_responses=True)

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        blocked_until = self._r.hget(f"af:{key}", "blocked_until")
        if blocked_until is None:
            return False
        return current_time < float(blocked_until)

    def record_failure(self, key: str, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        redis_key = f"af:{key}"
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(f"aff:{key}", 0, current_time - self._window_seconds)
        pipe.zadd(f"aff:{key}", {str(uuid4()): current_time})
        pipe.zcard(f"aff:{key}")
        pipe.expire(f"aff:{key}", self._window_seconds + 1)
        _, _, count, _ = pipe.execute()
        if count >= self._limit:
            blocked_until = current_time + self._block_seconds
            self._r.hset(redis_key, "blocked_until", blocked_until)
            self._r.expire(redis_key, self._block_seconds + 60)

    def reset(self, key: str) -> None:
        self._r.delete(f"af:{key}", f"aff:{key}")


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        settings: Settings,
        limiter=None,
        failure_tracker=None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        if limiter is None:
            try:
                limiter = RedisRateLimiter(settings.rate_limit_per_minute)
            except Exception:
                limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)
        self._limiter = limiter
        self._limiters_by_limit: dict[int, object] = {settings.rate_limit_per_minute: self._limiter}
        if failure_tracker is None:
            try:
                failure_tracker = RedisFailureTracker(
                    settings.auth_failure_limit,
                    settings.auth_failure_block_seconds,
                )
            except Exception:
                failure_tracker = AuthFailureTracker(
                    settings.auth_failure_limit,
                    settings.auth_failure_block_seconds,
                )
        self._failure_tracker = failure_tracker
        self._allowed_hosts = {host.lower() for host in settings.allowed_hosts}
        self._api_keys = ApiKeyService()
        # P1.1: per-tenant rate limiter (cumulative across all keys of
        # the same tenant). Defaults to 200 RPM — matches 16 GPU slots
        # at 1 req/s + 40% headroom.
        try:
            from app.middleware.tenant_rate_limit import make_tenant_rate_limiter
            self._tenant_limiter = make_tenant_rate_limiter(
                default_rpm=settings.tenant_rate_limit_rpm
            )
        except Exception as exc:
            logger.warning("Tenant rate limiter init failed: %s", exc)
            from app.middleware.tenant_rate_limit import InMemoryTenantRateLimiter
            self._tenant_limiter = InMemoryTenantRateLimiter(
                default_rpm=settings.tenant_rate_limit_rpm
            )

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        path = request.url.path
        if (
            path == "/"
            or path.startswith("/admin")
            or path.endswith(".html")
            or path.startswith("/static")
            or path in PWA_ROOT_FILES
            or path.endswith(PWA_ROOT_SUFFIXES)
        ):
            return await call_next(request)

        client_ip = self._client_ip(request)
        is_loopback = client_ip in ("127.0.0.1", "::1", "localhost")
        if not self._host_allowed(request):
            self._log_denial(client_ip, request.url.path, "host_not_allowed")
            return JSONResponse(
                status_code=status.HTTP_421_MISDIRECTED_REQUEST,
                content={"detail": "host not allowed"},
            )

        if request.method == "OPTIONS" or self._is_public_path(request.url.path):
            return await call_next(request)

        if not is_loopback and self._failure_tracker.is_blocked(client_ip) and request.url.path != "/admin.html":
            self._log_denial(client_ip, request.url.path, "client_temporarily_blocked")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "client temporarily blocked"},
            )

        provided_key = request.headers.get(API_KEY_HEADER)
        api_key_record: ApiKeyRecord | None = None
        if provided_key is not None and hmac.compare_digest(provided_key, self._settings.api_key or ""):
            request.state.api_key_id = None
            request.state.api_key_tenant_id = None
            request.state.api_key_allow_external = True
            request.state.api_key_rpm_limit = self._settings.rate_limit_per_minute
            request.state.api_key_allowed_projects = list(self._settings.allowed_projects) or None
            request.state.api_key_is_admin = True
        elif provided_key:
            api_key_record = self._api_keys.lookup(provided_key)
            if api_key_record is None:
                if not is_loopback:
                    self._failure_tracker.record_failure(client_ip)
                self._audit_auth_failure(client_ip, provided_key)
                self._log_denial(client_ip, request.url.path, "invalid_api_key")
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "invalid api key"},
                )
            request.state.api_key_id = api_key_record.id
            request.state.api_key_tenant_id = api_key_record.tenant_id
            request.state.api_key_allow_external = api_key_record.allow_external
            request.state.api_key_rpm_limit = api_key_record.rpm_limit
            request.state.api_key_allowed_projects = api_key_record.allowed_projects
            request.state.api_key_is_admin = bool(getattr(api_key_record, "is_admin", False))
        else:
            if not is_loopback:
                self._failure_tracker.record_failure(client_ip)
            self._audit_auth_failure(client_ip, provided_key=None)
            self._log_denial(client_ip, request.url.path, "invalid_api_key")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "invalid api key"},
            )

        if not is_loopback:
            self._failure_tracker.reset(client_ip)
        rate_key = self._rate_limit_key(
            client_ip,
            provided_key,
            getattr(request.state, "api_key_id", None),
        )
        limiter = self._limiter_for_limit(getattr(request.state, "api_key_rpm_limit", self._settings.rate_limit_per_minute))
        if not limiter.allow(rate_key):
            self._audit_rate_limit(rate_key, limiter)
            self._log_denial(client_ip, request.url.path, "rate_limit_exceeded")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
            )

        # P1.1: per-tenant rate limit (cumulative). Runs AFTER the
        # per-key check so a single abusive key still hits the per-key
        # limit first; this catches the "1000 keys, 60 RPM each"
        # fan-out attack. Tenant ID is set on request.state above.
        tenant_id = getattr(request.state, "api_key_tenant_id", None)
        if tenant_id and not self._tenant_limiter.allow(tenant_id):
            self._log_denial(client_ip, request.url.path, "tenant_rate_limit_exceeded")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "tenant rate limit exceeded"},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
            )

        if api_key_record is not None and api_key_record.monthly_budget_usd is not None:
            spent = ApiKeyService.get_monthly_spend(api_key_record.id)
            if spent >= api_key_record.monthly_budget_usd:
                self._log_denial(client_ip, request.url.path, "monthly_budget_exceeded")
                return JSONResponse(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    content={"detail": "monthly budget exceeded"},
                )

        return await call_next(request)

    def _host_allowed(self, request: Request) -> bool:
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        return "*" in self._allowed_hosts or host in self._allowed_hosts

    def _limiter_for_limit(self, limit: int) -> object:
        if limit not in self._limiters_by_limit:
            try:
                self._limiters_by_limit[limit] = RedisRateLimiter(limit)
            except Exception:
                self._limiters_by_limit[limit] = InMemoryRateLimiter(limit)
        return self._limiters_by_limit[limit]

    @staticmethod
    def _rate_limit_key(client_ip: str, provided_key: str | None, api_key_id: str | None) -> str:
        """Build a stable per-client rate key without persisting raw API secrets."""
        key_material = api_key_id or provided_key or "missing"
        key_hash = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        return f"{client_ip}:{key_hash}"

    def _is_public_path(self, path: str) -> bool:
        if path == "/admin.html":
            return True
        if path in ALWAYS_PUBLIC_PATHS:
            return True
        if self._settings.public_health_enabled and path in HEALTH_PATHS:
            return True
        if self._settings.public_docs_enabled and path in DOCS_PATHS:
            return True
        return False

    def _client_ip(self, request: Request) -> str:
        direct_ip = request.client.host if request.client is not None else "unknown"
        trusted_proxies = set(self._settings.trusted_proxy_ips or [])
        is_trusted_proxy = direct_ip in trusted_proxies or direct_ip in ("127.0.0.1", "::1", "localhost")
        if is_trusted_proxy:
            forwarded_for = request.headers.get("cf-connecting-ip") or request.headers.get("x-real-ip")
            if forwarded_for:
                return forwarded_for.split(",", 1)[0].strip()
        return direct_ip

    def _log_denial(self, client_ip: str, path: str, reason: str) -> None:
        logger.warning("security_denied ip=%s path=%s reason=%s", client_ip, path, reason)

    def _audit_auth_failure(self, client_ip: str, provided_key: str | None) -> None:
        """Best-effort PG snapshot of the auth-failure event (Rank 7 fix).

        We never log the raw provided_key — only its SHA-256 (matches the
        rate-limit key shape). For loopback probes the failure tracker
        is intentionally not consulted, but the PG audit row is still
        written so audit queries see uniform coverage.
        """
        try:
            from app.services import security_audit
            key_material = provided_key or f"ip:{client_ip}"
            key_hash = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:32]
            snapshot_key = f"af:{client_ip}:{key_hash}"
            blocked_until = 0.0
            if hasattr(self._failure_tracker, "_states"):
                # AuthFailureTracker (in-process). Best-effort read of block state.
                state = self._failure_tracker._states.get(client_ip)
                if state is not None:
                    blocked_until = float(state.blocked_until)
            security_audit.record_auth_failure(
                key=snapshot_key,
                failures=None,
                blocked_until=blocked_until,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("audit_auth_failure skipped: %s", exc)

    def _audit_rate_limit(self, rate_key: str, limiter: object) -> None:
        """Best-effort PG snapshot of the rate-limit denial (Rank 7 fix).

        For RedisRateLimiter we read the current ZSET timestamps so the
        audit row reflects the actual bucket state. For the in-memory
        limiter we use the current timestamp as a single-event marker
        (the deque lives off-process and is volatile anyway).
        """
        try:
            from app.services import security_audit
            current_time = time.time()
            timestamps: list[float] | None = None
            if isinstance(limiter, RedisRateLimiter):
                try:
                    raw = limiter._r.zrange(f"rl:{rate_key}", 0, -1, withscores=True)
                    timestamps = [float(score) for _member, score in raw]
                except Exception:
                    timestamps = None
            elif isinstance(limiter, InMemoryRateLimiter):
                bucket = limiter._buckets.get(rate_key)
                if bucket is not None:
                    with bucket.lock:
                        timestamps = list(bucket.timestamps)
            security_audit.record_rate_limit(
                key=rate_key,
                timestamps=timestamps,
                now=current_time,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("audit_rate_limit skipped: %s", exc)
