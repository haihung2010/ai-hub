"""Load monitor — probes llama-server /health?include_slots=1 per port.

Returns per-port saturation ∈ [0.0, 1.0]. Uses in-process cache with
configurable TTL to avoid hammering llama-server. On probe failure,
returns 0.0 (assume idle, don't over-degrade).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

logger = logging.getLogger(__name__)


def _parse_saturation(payload: dict) -> float:
    """Parse /health?include_slots=1 JSON into saturation ∈ [0.0, 1.0]."""
    slots = payload.get("slots", [])
    if not slots:
        return 0.0
    busy = sum(1 for s in slots if s.get("state") == 1)
    return busy / len(slots)


# Default llama-server URLs (overridable in tests)
DEFAULT_PORTS: dict[int, str] = {
    8080: "http://127.0.0.1:8080",  # 12B Q4 primary
    8081: "http://127.0.0.1:8081",  # E2B-bg
    8082: "http://127.0.0.1:8082",  # E4B
}


class LoadMonitor:
    """Per-port saturation cache with TTL.

    Threading: uses simple dict mutation; concurrent reads/writes are
    acceptable because the cache only stores immutable (sat, expiry)
    tuples and the worst case is one stale read.
    """

    def __init__(
        self,
        cache_ttl_seconds: float = 0.2,
        timeout_seconds: float = 1.0,
        ports: dict[int, str] | None = None,
    ) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout_seconds
        self._ports = ports or DEFAULT_PORTS
        self._cache: dict[int, tuple[float, float]] = {}  # port -> (sat, expiry)

    def get_saturation(
        self,
        port: int,
        probe_fn: Callable[[str], dict] | None = None,
    ) -> float:
        """Return saturation for `port` ∈ [0.0, 1.0].

        Args:
            port: llama-server port (8080/8081/8082).
            probe_fn: Optional override for testing. If None, uses
                httpx to fetch /health?include_slots=1.
        """
        now = time.monotonic()
        cached = self._cache.get(port)
        if cached is not None and cached[1] > now:
            return cached[0]

        url = self._ports.get(port, f"http://127.0.0.1:{port}")
        try:
            if probe_fn is not None:
                payload = probe_fn(url)
            else:
                payload = self._probe(url)
            sat = _parse_saturation(payload)
        except Exception as e:
            logger.warning("load_monitor: probe %s failed: %s — assuming 0.0", url, e)
            sat = 0.0

        self._cache[port] = (sat, now + self._cache_ttl)
        return sat

    def get_all_saturations(self) -> dict[int, float]:
        """Return current saturation for all known ports."""
        return {port: self.get_saturation(port) for port in self._ports}

    def _probe(self, url: str) -> dict:
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(f"{url}/health")
            r.raise_for_status()
            return r.json()
