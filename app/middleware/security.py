"""API key auth, rate limiting, and security audit logging middleware."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings

logger = logging.getLogger("app.security")
API_KEY_HEADER = "X-API-KEY"
EXCLUDED_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
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


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, limiter: InMemoryRateLimiter | None = None) -> None:
        super().__init__(app)
        self._settings = settings
        self._limiter = limiter or InMemoryRateLimiter(settings.rate_limit_per_minute)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        if request.method == "OPTIONS" or request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        provided_key = request.headers.get(API_KEY_HEADER)
        client_ip = request.client.host if request.client is not None else "unknown"

        if provided_key != self._settings.api_key:
            self._log_denial(client_ip, request.url.path, "invalid_api_key")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "invalid api key"},
            )

        if not self._limiter.allow(provided_key):
            self._log_denial(client_ip, request.url.path, "rate_limit_exceeded")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
            )

        return await call_next(request)

    def _log_denial(self, client_ip: str, path: str, reason: str) -> None:
        logger.warning("security_denied ip=%s path=%s reason=%s", client_ip, path, reason)
