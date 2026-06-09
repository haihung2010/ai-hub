"""State persistence for realistic-day test.

JSONL append-only with reload on startup. Stores per-user state (intent, questions,
summaries) so user can resume across cycles.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT_DIR = Path("/home/hung/ai-hub/reports/realistic-day-2026-06-08")


class State:
    def __init__(self, report_dir: Path = REPORT_DIR):
        self.report_dir = report_dir
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / "cycles").mkdir(exist_ok=True)
        self.cycle_file = self.report_dir / "state_cycle.json"
        self.users_file = self.report_dir / "state_users.jsonl"
        self.summaries_file = self.report_dir / "state_summaries.jsonl"
        self.adaptive_log = self.report_dir / "adaptive_scaler.log"
        self.stop_signal = self.report_dir / "stop_signal.txt"
        self.pid_file = self.report_dir / "pid"

        # In-memory state
        self.cycle: int = 0
        self.users: Dict[str, Dict[str, Any]] = {}  # user_name -> {topic, questions, intent_ids, last_summary, last_active_cycle}
        self.adaptive_history: List[Dict[str, Any]] = []
        self.cycle_user_count: Dict[int, int] = {}  # cycle -> user count spawned
        self.wave_user_count: Dict[int, int] = {}   # wave -> user count spawned
        self.summarized_waves: set = set()

        # Load if exists
        self._load()

    def _load(self) -> None:
        if self.cycle_file.exists():
            try:
                d = json.loads(self.cycle_file.read_text())
                self.cycle = d.get("cycle", 0)
            except Exception:
                self.cycle = 0
        if self.users_file.exists():
            for line in self.users_file.read_text().splitlines():
                if line.strip():
                    try:
                        d = json.loads(line)
                        self.users[d["user"]] = d["state"]
                    except Exception:
                        pass

    def save_cycle(self) -> None:
        self.cycle_file.write_text(json.dumps({
            "cycle": self.cycle,
            "wave_user_count": self.wave_user_count,
        }, indent=2))

    def save_wave(self, wave: int) -> None:
        # Reuse save_cycle which also writes wave_user_count
        self.save_cycle()

    def add_user(self, user: str, topic: str, questions: List[str]) -> None:
        intent_ids = [f"{topic}_q{i:02d}" for i in range(len(questions))]
        self.users[user] = {
            "topic": topic,
            "questions": questions,
            "intent_ids": intent_ids,
            "asked": [],          # list of (cycle, intent_id, ts, latency, status)
            "last_summary": "",
            "first_active_cycle": self.cycle,
            "last_active_cycle": self.cycle,
        }
        self._append_user(user)

    def _append_user(self, user: str) -> None:
        with self.users_file.open("a") as f:
            f.write(json.dumps({"user": user, "state": self.users[user]}, ensure_ascii=False) + "\n")

    def update_user(self, user: str, **kwargs) -> None:
        if user not in self.users:
            return
        self.users[user].update(kwargs)
        # Rewrite this user's line in users_file
        lines = []
        if self.users_file.exists():
            for line in self.users_file.read_text().splitlines():
                if line.strip():
                    try:
                        d = json.loads(line)
                        if d.get("user") != user:
                            lines.append(line)
                    except Exception:
                        pass
        lines.append(json.dumps({"user": user, "state": self.users[user]}, ensure_ascii=False))
        self.users_file.write_text("\n".join(lines) + "\n")

    def record_asked(self, user: str, cycle: int, intent_id: str, ts: str, latency_ms: int, status: int) -> None:
        if user not in self.users:
            return
        self.users[user]["asked"].append({
            "cycle": cycle, "intent_id": intent_id, "ts": ts,
            "latency_ms": latency_ms, "status": status,
        })
        self.users[user]["last_active_cycle"] = cycle
        # Persist: rewrite the user's line in users_file so state survives restart.
        # update_user reads+writes the whole file (O(N_lines) per call) — at 232 req/min
        # and ~4k users this is ~16MB/s I/O, fine on SSD. If this becomes a bottleneck,
        # switch to per-user file or a delta-log with periodic compaction.
        self.update_user(user)

    def users_first_seen_before(self, cycle: int) -> List[str]:
        return [u for u, s in self.users.items() if s.get("first_active_cycle", 0) < cycle]

    def log_adaptive(self, decision: Dict[str, Any]) -> None:
        line = f"[{decision.get('ts','')}] {decision.get('action','')}: concurrency={decision.get('concurrency',0)} reason={decision.get('reason','')}"
        with self.adaptive_log.open("a") as f:
            f.write(line + "\n")
        self.adaptive_history.append(decision)

    def should_stop(self) -> bool:
        return self.stop_signal.exists()

    def cycle_log_path(self, cycle: int) -> Path:
        return self.report_dir / "cycles" / f"cycle_{cycle:02d}.jsonl"


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out
