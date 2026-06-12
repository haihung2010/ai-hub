#!/usr/bin/env python3
"""AI Hub Comprehensive 30-Minute Test.

Stress-tests ai-hub across 4 dimensions:
  1. Functional response (chat + latency)
  2. Memory under load (10 user × 10 câu rotate, 100 user)
  3. Memory persistence (return after 2-3 min, check recall)
  4. Multi-tenant isolation (verified via user scoping)

E-commerce clothing domain, 5 cache topics for same-topic speedup test.

Usage:
  python scripts/test_comprehensive_30min.py                # full 30-min test
  python scripts/test_comprehensive_30min.py --quick        # 5-min smoke (5 user × 5 q)
  python scripts/test_comprehensive_30min.py --phases 1,2   # only phase 1+2
  python scripts/test_comprehensive_30min.py --dry-run      # synthetic data, no HTTP

Output:
  reports/comprehensive_30min_<ts>.json   — full metrics + pass/fail
  reports/comprehensive_30min_<ts>.log    — request/response log (errors only)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

# ── Config ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    llama_url: str
    concurrency: int
    phase1_turns_per_user: int
    phase2_users_total: int
    phase2_turns_per_user: int
    phase3_rounds: int
    phase3_users_per_round: int
    phase3_gap_seconds: int
    kb_card_count: int
    report_dir: Path
    error_rate_threshold: float
    memory_recall_threshold: float
    cache_speedup_threshold: float
    total_runtime_cap_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        api_key = ""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        with open(env_path) as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
        if not api_key:
            raise RuntimeError(f"API_KEY not found in {env_path}")
        return cls(
            base_url=os.getenv("AIHUB_TEST_BASE_URL", "http://127.0.0.1:8000"),
            api_key=api_key,
            llama_url=os.getenv("AIHUB_TEST_LLAMA_URL", "http://127.0.0.1:8080"),
            concurrency=int(os.getenv("AIHUB_TEST_CONCURRENCY", "4")),
            phase1_turns_per_user=int(os.getenv("AIHUB_TEST_PHASE1_TURNS", "10")),
            phase2_users_total=int(os.getenv("AIHUB_TEST_PHASE2_USERS", "100")),
            phase2_turns_per_user=int(os.getenv("AIHUB_TEST_PHASE2_TURNS", "10")),
            phase3_rounds=int(os.getenv("AIHUB_TEST_PHASE3_ROUNDS", "3")),
            phase3_users_per_round=int(os.getenv("AIHUB_TEST_PHASE3_USERS", "10")),
            phase3_gap_seconds=int(os.getenv("AIHUB_TEST_PHASE3_GAP", "150")),
            kb_card_count=int(os.getenv("AIHUB_TEST_KB_CARDS", "75")),
            report_dir=Path(os.getenv("AIHUB_TEST_REPORT_DIR", "reports")),
            error_rate_threshold=float(os.getenv("AIHUB_TEST_ERROR_THRESHOLD", "0.05")),
            memory_recall_threshold=float(os.getenv("AIHUB_TEST_RECALL_THRESHOLD", "0.70")),
            cache_speedup_threshold=float(os.getenv("AIHUB_TEST_CACHE_SPEEDUP", "0.10")),
            total_runtime_cap_seconds=int(os.getenv("AIHUB_TEST_RUNTIME_CAP", "2100")),
        )

    def headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
