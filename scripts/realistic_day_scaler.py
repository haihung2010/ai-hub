"""Realistic-day adaptive scaler.

Reads /v1/admin/queue, /v1/admin/usage, nvidia-smi VRAM, makes scaling decisions.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _gpu_mem_used_mib() -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            timeout=2
        ).decode().strip().splitlines()
        if out:
            return int(out[0])
    except Exception:
        return None
    return None


def _api_get(path: str, key: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:8000{path}",
            headers={"X-API-KEY": key},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


@dataclass
class ScalerConfig:
    queue_high: int = 8
    queue_low: int = 3
    p95_high_s: float = 25.0
    p95_low_s: float = 15.0
    vram_target_mib: int = 14_500      # below this = can scale up
    vram_danger_mib: int = 15_400      # above this = scale down 30%
    vram_critical_mib: int = 16_000    # critical
    step_up: float = 0.20
    step_down: float = 0.20
    step_emergency: float = 0.30
    cap_max: int = 200
    cap_min: int = 1


class AdaptiveScaler:
    def __init__(self, state, api_key: str, cfg: Optional[ScalerConfig] = None):
        self.state = state
        self.api_key = api_key
        self.cfg = cfg if cfg is not None else ScalerConfig()
        self.concurrency: int = 20
        self._latencies: deque = deque(maxlen=200)  # rolling latencies for p95
        self._last_decision: dict = {}

    def observe(self, latency_ms: int, status: int) -> None:
        if status == 200 and 0 < latency_ms < 120_000:
            self._latencies.append(latency_ms / 1000.0)

    def _p95(self) -> Optional[float]:
        if len(self._latencies) < 5:
            return None
        s = sorted(self._latencies)
        idx = int(len(s) * 0.95)
        return s[idx]

    def step(self) -> dict:
        """Run one scaler iteration. Returns the decision dict."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        queue = _api_get("/v1/admin/queue", self.api_key) or {}
        vram = _gpu_mem_used_mib()
        p95 = self._p95()
        q_wait = queue.get("waiting", 0) or 0
        q_active = queue.get("active", 0) or 0

        action = "hold"
        reason = f"q_wait={q_wait} q_active={q_active} p95={p95} vram={vram}"
        old = self.concurrency

        # Emergency: VRAM critical
        if vram is not None and vram >= self.cfg.vram_danger_mib:
            new = max(self.cfg.cap_min, int(self.concurrency * (1 - self.cfg.step_emergency)))
            if new < self.concurrency:
                self.concurrency = new
                action = "emergency_down"
                reason = f"VRAM={vram}MIB >= {self.cfg.vram_danger_mib}MIB"

        # Scale down: queue high OR p95 high
        elif q_wait > self.cfg.queue_high or (p95 is not None and p95 > self.cfg.p95_high_s):
            new = max(self.cfg.cap_min, int(self.concurrency * (1 - self.cfg.step_down)))
            if new < self.concurrency:
                self.concurrency = new
                action = "scale_down"
                reason = f"q_wait={q_wait} p95={p95}"

        # Scale up: queue low + p95 low + VRAM ok
        elif (
            q_wait < self.cfg.queue_low
            and (p95 is None or p95 < self.cfg.p95_low_s)
            and (vram is None or vram < self.cfg.vram_target_mib)
            and self.concurrency < self.cfg.cap_max
        ):
            new = min(self.cfg.cap_max, int(self.concurrency * (1 + self.cfg.step_up)) + 1)
            if new > self.concurrency:
                self.concurrency = new
                action = "scale_up"
                reason = f"q_wait={q_wait} p95={p95} vram={vram} <targets>"

        decision = {
            "ts": ts,
            "action": action,
            "old": old,
            "new": self.concurrency,
            "concurrency": self.concurrency,
            "q_wait": q_wait,
            "q_active": q_active,
            "p95": p95,
            "vram_mib": vram,
            "reason": reason,
        }
        self._last_decision = decision
        if action != "hold":
            self.state.log_adaptive(decision)
        return decision
