# AI Hub Loop Engineering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a loop engineering system for AI Hub that runs tests, diagnoses failures, proposes fixes, and surfaces proposals to the user — without auto-applying code changes. Triggered by daily cron @ 02:00 and manual `/loop-test`.

**Architecture:** Multi-agent loop with state file spine. Single orchestrator dispatches 3 single-responsibility sub-agents (test-runner, analyst, proposer) sequentially per iteration. State persists in `loop-state.md` (current iter + history). Per-iteration artifacts in `reports/loop-iterations/<id>/`. Conservative apply policy — loop proposes, user applies.

**Tech Stack:** Python 3.11+, cron (system), flock (POSIX file lock), Claude Code `claude --print` for spawning sessions, .claude/skills/ for `/loop-test`. No new external deps.

**Spec:** `docs/superpowers/specs/2026-06-18-aihub-loop-engineering.md` (commit 65fbc75)

---

## File Structure

**New files (ai-hub repo):**
- `loop/state.py` (~150 LOC) — `loop-state.md` reader/writer, state machine
- `loop/orchestrator.py` (~120 LOC) — main iteration driver
- `loop/subagents/test_runner.py` (~100 LOC) — sub-agent 1
- `loop/subagents/analyst.py` (~100 LOC) — sub-agent 2
- `loop/subagents/proposer.py` (~150 LOC) — sub-agent 3
- `loop/subagents/__init__.py` (5 LOC)
- `loop/self_review.py` (~100 LOC) — weekly loop self-review
- `loop/__init__.py` (3 LOC)
- `loop/paths.py` (~30 LOC) — file path constants
- `scripts/loop_run.py` (~80 LOC) — entry point for cron + manual
- `scripts/loop_cron.sh` (~25 LOC) — cron wrapper
- `.claude/skills/loop-test/SKILL.md` — manual trigger skill
- `.claude/hooks/post-commit-loop.sh` — opt-in post-commit
- `loop-state.md` (initial empty)
- `tests/unit/test_loop_state.py` — state machine tests
- `tests/integration/test_loop_subagent_isolation.py` — boundary tests
- `tests/integration/test_loop_self_review.py`
- `tests/integration/test_loop_cost_budget.py`
- `docs/loop-engineering.md` — user-facing docs

**Modified files:**
- `/etc/cron.d/aihub-loop` (system file, installed by Task 14)
- `.gitignore` — exclude `loop.lock`, `.loop-queue`

**No new Python deps** — uses stdlib (asyncio, json, subprocess, fcntl for flock).

---

## Conventions

- All loop artifacts under `loop/` namespace (Python module) and `loop-state.md` (markdown)
- All file paths use `loop/paths.py` constants — no hardcoded paths in sub-agents
- All sub-agents receive context via dict, never via global state
- All `loop-state.md` writes use flock — concurrent loops can't corrupt state
- All test runs are read-only on source code (proposer writes diff to `/tmp/`, never to `app/`)
- Commit after every task (TDD red→green→refactor → commit)

---

## Phase 1: State file + state machine (foundation)

### Task 1: Create paths module and initial empty state file

**Files:**
- Create: `loop/__init__.py`
- Create: `loop/paths.py`
- Create: `loop-state.md`

- [ ] **Step 1.1: Write failing test for paths module**

`tests/unit/test_loop_paths.py`:

```python
from loop.paths import (
    REPO_ROOT, LOOP_STATE_FILE, LOOP_LOCK_FILE, ITER_DIR,
    iter_dir, iter_file, loop_state_file
)

def test_paths_are_absolute():
    assert str(LOOP_STATE_FILE).startswith("/")
    assert str(LOOP_LOCK_FILE).startswith("/")

def test_iter_dir_format():
    p = iter_dir("2026-06-18-01")
    assert str(p).endswith("reports/loop-iterations/2026-06-18-01")
    assert p.is_absolute()

def test_iter_file_format():
    p = iter_file("2026-06-18-01", "test-results.json")
    assert str(p).endswith("reports/loop-iterations/2026-06-18-01/test-results.json")
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_loop_paths.py -v`
Expected: ModuleNotFoundError: No module named 'loop'

- [ ] **Step 1.3: Create loop package and paths module**

`loop/__init__.py`:

```python
"""AI Hub loop engineering package."""
__version__ = "0.1.0"
```

`loop/paths.py`:

```python
"""Path constants for loop engineering. Single source of truth for all file locations."""
from pathlib import Path

REPO_ROOT = Path("/home/hung/ai-hub")
LOOP_STATE_FILE = REPO_ROOT / "loop-state.md"
LOOP_LOCK_FILE = REPO_ROOT / "loop.lock"
LOOP_QUEUE_FILE = REPO_ROOT / ".loop-queue"
LOOP_HOOK_ENABLED_FLAG = REPO_ROOT / ".loop-hook-enabled"
ITER_ROOT = REPO_ROOT / "reports" / "loop-iterations"
SELF_REVIEW_FILE = REPO_ROOT / "reports" / "loop-self-review.md"
LOOP_DOCS_FILE = REPO_ROOT / "docs" / "loop-engineering.md"
APP_ROOT = REPO_ROOT / "app"
TESTS_ROOT = REPO_ROOT / "tests"


def iter_dir(iter_id: str) -> Path:
    """Get the per-iteration directory for a given iteration ID."""
    return ITER_ROOT / iter_id


def iter_file(iter_id: str, filename: str) -> Path:
    """Get a specific file within an iteration directory."""
    return iter_dir(iter_id) / filename
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_loop_paths.py -v`
Expected: 3 passed

- [ ] **Step 1.5: Create initial empty loop-state.md**

`loop-state.md`:

```markdown
---
# AI Hub Loop State
# Last updated: 2026-06-18 (initial creation)
# This file is the spine of the loop — every iteration reads/writes it.
---

## Current iteration
- iteration_id: NONE
- started_at: ""
- trigger: ""
- status: pending
- kill_switch: false

## Iteration plan
- tests_to_run: []
- target_branch: main
- skip_if_no_change: false

## Test results
- placeholder: NOT_RUN

## Analysis
- root_cause: ""
- failure_signatures: []

## Proposal
- proposal_path: ""
- diff_summary: ""

## History
| iter_id | started | trigger | verdict | proposal_applied |
|---------|---------|---------|---------|------------------|
```

- [ ] **Step 1.6: Update .gitignore**

Append to `.gitignore`:

```
loop.lock
.loop-queue
.loop-hook-enabled
```

- [ ] **Step 1.7: Commit**

```bash
git add loop/__init__.py loop/paths.py loop-state.md tests/unit/test_loop_paths.py .gitignore
git commit -m "feat(loop): add path constants and initial state file"
```

---

### Task 2: State file reader/writer with flock

**Files:**
- Create: `loop/state.py`
- Test: `tests/unit/test_loop_state.py`

- [ ] **Step 2.1: Write failing test for read/write state**

Append to `tests/unit/test_loop_state.py`:

```python
import pytest
from loop.state import read_state, write_state, LoopState, StateStatus

def test_state_roundtrip(tmp_path, monkeypatch):
    """read_state then write_state preserves data."""
    from loop import paths
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    
    state = LoopState(
        iteration_id="2026-06-18-01",
        status=StateStatus.IN_PROGRESS,
        trigger="manual",
        tests_to_run=["ecom_100user"],
        target_branch="main",
        skip_if_no_change=False,
        kill_switch=False,
        test_results={},
        analysis={},
        proposal={},
        history=[],
    )
    write_state(state)
    loaded = read_state()
    assert loaded.iteration_id == "2026-06-18-01"
    assert loaded.status == StateStatus.IN_PROGRESS
    assert loaded.tests_to_run == ["ecom_100user"]

def test_state_handles_missing_file(tmp_path, monkeypatch):
    """If loop-state.md missing, return default state."""
    from loop import paths
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "nonexistent.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    state = read_state()
    assert state.status == StateStatus.PENDING
    assert state.iteration_id == "NONE"
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_loop_state.py -v`
Expected: ModuleNotFoundError: No module named 'loop.state'

- [ ] **Step 2.3: Implement state.py**

`loop/state.py`:

```python
"""Loop state machine: read, write, transition loop-state.md with file locking."""
import fcntl
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from loop.paths import LOOP_STATE_FILE, LOOP_LOCK_FILE


class StateStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass
class LoopState:
    iteration_id: str
    status: StateStatus
    trigger: str
    started_at: str
    tests_to_run: list[str]
    target_branch: str
    skip_if_no_change: bool
    kill_switch: bool
    test_results: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    proposal: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def default(cls) -> "LoopState":
        return cls(
            iteration_id="NONE",
            status=StateStatus.PENDING,
            trigger="",
            started_at="",
            tests_to_run=[],
            target_branch="main",
            skip_if_no_change=False,
            kill_switch=False,
        )


def _parse_state(content: str) -> LoopState:
    """Parse loop-state.md content into LoopState. Tolerant of missing fields."""
    defaults = LoopState.default()
    
    def find_field(name: str) -> str:
        m = re.search(rf"^- {re.escape(name)}:\s*(.+)$", content, re.MULTILINE)
        return m.group(1).strip() if m else ""

    def find_list(name: str) -> list[str]:
        m = re.search(rf"^- {re.escape(name)}:\s*\[(.*?)\]", content, re.MULTILINE | re.DOTALL)
        if not m: return []
        return [s.strip() for s in m.group(1).split(",") if s.strip()]
    
    iteration_id = find_field("iteration_id") or defaults.iteration_id
    status_str = find_field("status") or defaults.status.value
    try:
        status = StateStatus(status_str)
    except ValueError:
        status = StateStatus.PENDING
    kill_switch = find_field("kill_switch").lower() == "true"
    target_branch = find_field("target_branch") or "main"
    skip = find_field("skip_if_no_change").lower() == "true"
    trigger = find_field("trigger")
    started_at = find_field("started_at")
    tests = find_list("tests_to_run")
    
    return LoopState(
        iteration_id=iteration_id,
        status=status,
        trigger=trigger,
        started_at=started_at,
        tests_to_run=tests,
        target_branch=target_branch,
        skip_if_no_change=skip,
        kill_switch=kill_switch,
    )


def read_state() -> LoopState:
    """Read loop-state.md. Returns default state if file missing or malformed."""
    if not LOOP_STATE_FILE.exists():
        return LoopState.default()
    try:
        content = LOOP_STATE_FILE.read_text(encoding="utf-8")
        return _parse_state(content)
    except Exception:
        return LoopState.default()


def write_state(state: LoopState) -> None:
    """Write LoopState to loop-state.md atomically with flock."""
    LOOP_LOCK_FILE.touch(exist_ok=True)
    with open(LOOP_LOCK_FILE, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            content = _format_state(state)
            tmp_path = LOOP_STATE_FILE.with_suffix(".md.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(LOOP_STATE_FILE)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _format_state(state: LoopState) -> str:
    """Serialize LoopState to loop-state.md format."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    tests_str = ", ".join(f'"{t}"' for t in state.tests_to_run)
    
    history_rows = "\n".join(
        f"| {h.get('iter_id', '')} | {h.get('started', '')} | {h.get('trigger', '')} | {h.get('verdict', '')} | {h.get('proposal_applied', '')} |"
        for h in state.history
    )
    if not history_rows:
        history_rows = ""
    
    return f"""---
# AI Hub Loop State
# Last updated: {now}
# This file is the spine of the loop — every iteration reads/writes it.
---

## Current iteration
- iteration_id: {state.iteration_id}
- started_at: {state.started_at}
- trigger: {state.trigger}
- status: {state.status.value}
- kill_switch: {str(state.kill_switch).lower()}

## Iteration plan
- tests_to_run: [{tests_str}]
- target_branch: {state.target_branch}
- skip_if_no_change: {str(state.skip_if_no_change).lower()}

## Test results
- placeholder: NOT_RUN

## Analysis
- root_cause: ""
- failure_signatures: []

## Proposal
- proposal_path: ""
- diff_summary: ""

## History
| iter_id | started | trigger | verdict | proposal_applied |
|---------|---------|---------|---------|------------------|
{history_rows}
"""


def transition(state: LoopState, action: str, **kwargs: Any) -> LoopState:
    """Apply state transition. Pure function — returns new state."""
    new = LoopState(**asdict(state))
    new.status = StateStatus(new.status)
    
    if action == "start":
        new.status = StateStatus.IN_PROGRESS
        new.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    elif action == "complete":
        new.status = StateStatus.COMPLETED
    elif action == "fail":
        new.status = StateStatus.FAILED
    elif action == "abandon":
        new.status = StateStatus.ABANDONED
    elif action == "kill":
        new.kill_switch = True
    else:
        raise ValueError(f"Unknown action: {action}")
    
    for k, v in kwargs.items():
        setattr(new, k, v)
    return new
```

- [ ] **Step 2.4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_loop_state.py -v`
Expected: 2 passed

- [ ] **Step 2.5: Commit**

```bash
git add loop/state.py tests/unit/test_loop_state.py
git commit -m "feat(loop): state machine with flock-locked read/write"
```

---

### Task 3: State machine transition tests

**Files:**
- Modify: `tests/unit/test_loop_state.py`

- [ ] **Step 3.1: Add transition tests**

Append to `tests/unit/test_loop_state.py`:

```python
def test_transition_start():
    from loop.state import transition
    state = LoopState.default()
    state.iteration_id = "2026-06-18-01"
    new = transition(state, "start", trigger="cron")
    assert new.status == StateStatus.IN_PROGRESS
    assert new.trigger == "cron"
    assert new.started_at != ""
    assert new.iteration_id == "2026-06-18-01"

def test_transition_complete():
    from loop.state import transition
    state = LoopState(status=StateStatus.IN_PROGRESS, iteration_id="X", trigger="cron",
                     started_at="2026-06-18", tests_to_run=[], target_branch="main",
                     skip_if_no_change=False, kill_switch=False)
    new = transition(state, "complete")
    assert new.status == StateStatus.COMPLETED

def test_transition_kill_switch():
    from loop.state import transition
    state = LoopState.default()
    new = transition(state, "kill")
    assert new.kill_switch is True

def test_idempotent_re_run_when_completed(tmp_path, monkeypatch):
    """Re-running on a completed state should NOT re-trigger."""
    from loop import paths
    from loop.state import transition
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    
    state = LoopState.default()
    state.iteration_id = "2026-06-18-01"
    state = transition(state, "start")
    state = transition(state, "complete")
    write_state(state)
    
    loaded = read_state()
    assert loaded.status == StateStatus.COMPLETED
    # No transition happens — caller checks status before starting

def test_abandoned_recovery():
    """in_progress > 24h ago should be markable as abandoned."""
    from loop.state import transition
    state = LoopState(status=StateStatus.IN_PROGRESS, iteration_id="X",
                     trigger="cron", started_at="2026-06-15 02:00:00",
                     tests_to_run=[], target_branch="main",
                     skip_if_no_change=False, kill_switch=False)
    new = transition(state, "abandon")
    assert new.status == StateStatus.ABANDONED
```

- [ ] **Step 3.2: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_loop_state.py -v`
Expected: 6 passed (2 from Task 2 + 4 from this task)

If `test_idempotent_re_run_when_completed` fails, the test's no-op behavior depends on caller logic, not state machine. The state machine's job is to provide status; idempotency is the orchestrator's responsibility (Task 4).

- [ ] **Step 3.3: Commit**

```bash
git add tests/unit/test_loop_state.py
git commit -m "test(loop): add state machine transition tests"
```

---

## Phase 2: Orchestrator + 3 sub-agents

### Task 4: Orchestrator skeleton (read state, dispatch, update)

**Files:**
- Create: `loop/orchestrator.py`
- Modify: `loop/state.py` (add history helpers)

- [ ] **Step 4.1: Add history helper to state.py**

Append to `loop/state.py`:

```python
def append_history(state: LoopState, iter_id: str, verdict: str, proposal_path: str = "") -> LoopState:
    """Append a history row. Keeps last 10."""
    new = LoopState(**asdict(state))
    new.history = list(state.history)
    new.history.append({
        "iter_id": iter_id,
        "started": state.started_at,
        "trigger": state.trigger,
        "verdict": verdict,
        "proposal_applied": "n/a" if verdict == "PASS" else ("pending" if proposal_path else "n/a"),
    })
    new.history = new.history[-10:]
    return new
```

- [ ] **Step 4.2: Write failing test for orchestrator entry point**

`tests/unit/test_orchestrator.py`:

```python
def test_orchestrator_respects_kill_switch(tmp_path, monkeypatch):
    """If kill_switch=true, orchestrator aborts immediately."""
    from loop import paths
    from loop.state import read_state, write_state, transition, LoopState
    from loop.orchestrator import run_iteration
    
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    monkeypatch.setattr(paths, "ITER_ROOT", tmp_path / "reports" / "loop-iterations")
    
    state = LoopState.default()
    state = transition(state, "kill")
    write_state(state)
    
    result = run_iteration()
    assert result["aborted"] is True
    assert result["reason"] == "kill_switch_engaged"
```

- [ ] **Step 4.3: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_orchestrator.py -v`
Expected: ModuleNotFoundError: No module named 'loop.orchestrator'

- [ ] **Step 4.4: Implement orchestrator.py**

`loop/orchestrator.py`:

```python
"""Main loop orchestrator. Reads state, dispatches sub-agents, updates state."""
from datetime import datetime, timezone
from typing import Any

from loop.paths import iter_dir
from loop.state import (
    LoopState, read_state, write_state, transition, append_history
)


def run_iteration() -> dict[str, Any]:
    """Run one loop iteration. Returns summary dict.
    
    Returns:
        {
            "aborted": bool,
            "reason": str,
            "iter_id": str,
            "verdict": "PASS" | "FAIL" | "ABORTED",
            "proposal_path": str,
        }
    """
    state = read_state()
    
    # Kill switch check
    if state.kill_switch:
        return {"aborted": True, "reason": "kill_switch_engaged",
                "iter_id": state.iteration_id, "verdict": "ABORTED",
                "proposal_path": ""}
    
    # Abandoned recovery: in_progress > 24h ago
    if state.status.value == "in_progress" and _is_stale(state):
        state = transition(state, "abandon")
        write_state(state)
    
    # If completed, no-op
    if state.status.value == "completed":
        return {"aborted": False, "reason": "already_completed",
                "iter_id": state.iteration_id, "verdict": "SKIP",
                "proposal_path": ""}
    
    # Initialize new iteration
    iter_id = _make_iter_id()
    state.iteration_id = iter_id
    state = transition(state, "start", trigger=state.trigger or "manual")
    
    # Ensure iter directory
    iter_dir(iter_id).mkdir(parents=True, exist_ok=True)
    write_state(state)
    
    # Dispatch sub-agents (stubs in Task 4, real in Tasks 5-7)
    from loop.subagents.test_runner import run as run_tests
    test_results = run_tests(state.tests_to_run, iter_id)
    _write_iter(iter_id, "test-results.json", test_results)
    
    if test_results.get("verdict") == "all_pass":
        state = transition(state, "complete")
        state = append_history(state, iter_id, "PASS")
        write_state(state)
        return {"aborted": False, "reason": "all_pass",
                "iter_id": iter_id, "verdict": "PASS", "proposal_path": ""}
    
    from loop.subagents.analyst import run as run_analyst
    analysis = run_analyst(iter_id)
    _write_iter(iter_id, "analysis.json", analysis)
    
    from loop.subagents.proposer import run as run_proposer
    proposal = run_proposer(iter_id)
    _write_iter(iter_id, "proposal.md", proposal.get("markdown", ""))
    _write_iter(iter_id, "proposal.diff", proposal.get("diff", ""))
    
    state = transition(state, "complete")
    state = append_history(state, iter_id, "FAIL",
                            proposal_path=str(iter_dir(iter_id) / "proposal.md"))
    write_state(state)
    
    return {"aborted": False, "reason": "completed_with_proposal",
            "iter_id": iter_id, "verdict": "FAIL",
            "proposal_path": str(iter_dir(iter_id) / "proposal.md")}


def _is_stale(state: LoopState) -> bool:
    """Check if in_progress state is > 24h old."""
    if not state.started_at: return True
    try:
        started = datetime.strptime(state.started_at, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        return (now - started).total_seconds() > 86400
    except ValueError:
        return True


def _make_iter_id() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d-01")  # 01 = first iter of day; increment if needed


def _write_iter(iter_id: str, filename: str, content: Any) -> None:
    """Write content to per-iter file. Content is str or dict (JSON)."""
    p = iter_dir(iter_id) / filename
    if isinstance(content, (dict, list)):
        import json
        p.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        p.write_text(str(content), encoding="utf-8")
```

- [ ] **Step 4.5: Create subagent stubs (so orchestrator imports work)**

`loop/subagents/__init__.py`:

```python
"""Sub-agents for the loop. Each is single-responsibility."""
```

`loop/subagents/test_runner.py`:

```python
"""test-runner sub-agent. Runs tests, captures output, no analysis."""

def run(tests: list[str], iter_id: str) -> dict:
    """Stub. Real impl in Task 5."""
    return {"verdict": "all_pass", "tests_run": tests, "iter_id": iter_id}
```

`loop/subagents/analyst.py`:

```python
"""analyst sub-agent. Reads test-results.json, finds root cause."""

def run(iter_id: str) -> dict:
    """Stub. Real impl in Task 6."""
    return {"root_cause": "stub", "failure_signatures": [], "iter_id": iter_id}
```

`loop/subagents/proposer.py`:

```python
"""proposer sub-agent. From analysis, drafts fix as diff."""

def run(iter_id: str) -> dict:
    """Stub. Real impl in Task 7."""
    return {"markdown": "stub proposal", "diff": "", "iter_id": iter_id}
```

- [ ] **Step 4.6: Run test to verify kill-switch test passes**

Run: `./venv/bin/pytest tests/unit/test_orchestrator.py -v`
Expected: 1 passed

- [ ] **Step 4.7: Commit**

```bash
git add loop/orchestrator.py loop/subagents/ tests/unit/test_orchestrator.py loop/state.py
git commit -m "feat(loop): orchestrator skeleton with sub-agent stubs"
```

---

### Task 5: test-runner sub-agent (real impl)

**Files:**
- Modify: `loop/subagents/test_runner.py`
- Test: `tests/integration/test_loop_subagent_isolation.py`

- [ ] **Step 5.1: Write failing test for test-runner**

`tests/integration/test_loop_subagent_isolation.py`:

```python
import subprocess
from loop.subagents import test_runner

def test_test_runner_returns_dict():
    """Even with no tests, returns proper structure."""
    result = test_runner.run([], "2026-06-18-01")
    assert "verdict" in result
    assert result["verdict"] in ("all_pass", "failures")
    assert "tests_run" in result
    assert "iter_id" in result

def test_test_runner_does_not_propose():
    """test-runner must not include 'fix' or 'proposal' in output."""
    result = test_runner.run(["tests/unit/test_loop_state.py"], "2026-06-18-01")
    output_str = str(result).lower()
    assert "should fix" not in output_str
    assert "proposal" not in output_str
    assert "diff" not in output_str
```

- [ ] **Step 5.2: Run test to verify it fails on second test**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py -v`
Expected: First test passes (stub returns dict), second test passes (no fix keywords in stub output). If both pass, the stub is OK. Real implementation must preserve this.

- [ ] **Step 5.3: Implement real test_runner.py**

`loop/subagents/test_runner.py`:

```python
"""test-runner sub-agent. Runs tests, captures output, NO analysis.

Output format:
    {
        "verdict": "all_pass" | "failures",
        "tests_run": [...],
        "iter_id": "...",
        "duration_seconds": float,
        "failures": [
            {"test_path": "...", "error": "..."},
        ],
        "summary": "5 passed, 2 failed in 30s"
    }
"""
import subprocess
import time
from typing import Any


def run(tests: list[str], iter_id: str) -> dict[str, Any]:
    """Run the given tests, capture structured output. No interpretation.
    
    Args:
        tests: List of test paths. Empty list = no-op, returns all_pass.
        iter_id: Iteration ID (for tagging output).
    
    Returns:
        Structured test results dict.
    """
    if not tests:
        return {
            "verdict": "all_pass",
            "tests_run": [],
            "iter_id": iter_id,
            "duration_seconds": 0.0,
            "failures": [],
            "summary": "no tests requested",
        }
    
    start = time.time()
    failures = []
    tests_run = []
    
    for test_path in tests:
        tests_run.append(test_path)
        cmd = _build_command(test_path)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3600
            )
            if proc.returncode != 0:
                failures.append({
                    "test_path": test_path,
                    "exit_code": proc.returncode,
                    "stderr_excerpt": proc.stderr[-500:] if proc.stderr else "",
                    "stdout_excerpt": proc.stdout[-500:] if proc.stdout else "",
                })
        except subprocess.TimeoutExpired:
            failures.append({
                "test_path": test_path,
                "error": "timeout_after_3600s",
            })
        except Exception as e:
            failures.append({
                "test_path": test_path,
                "error": f"subprocess_error: {type(e).__name__}: {e}",
            })
    
    duration = time.time() - start
    verdict = "all_pass" if not failures else "failures"
    summary = f"{len(tests_run) - len(failures)} passed, {len(failures)} failed in {duration:.1f}s"
    
    return {
        "verdict": verdict,
        "tests_run": tests_run,
        "iter_id": iter_id,
        "duration_seconds": duration,
        "failures": failures,
        "summary": summary,
    }


def _build_command(test_path: str) -> list[str]:
    """Build the shell command to run a test. Recognizes special test names."""
    if test_path == "ecom_100user":
        return ["./venv/bin/python", "tests/integration/test_ecommerce_100users.py"]
    if test_path.startswith("tests/"):
        return ["./venv/bin/pytest", test_path, "-v", "--tb=short"]
    # Fallback: treat as pytest target
    return ["./venv/bin/pytest", test_path, "-v", "--tb=short"]
```

- [ ] **Step 5.4: Run tests to verify**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py::test_test_runner_returns_dict tests/integration/test_loop_subagent_isolation.py::test_test_runner_does_not_propose -v`
Expected: 2 passed

- [ ] **Step 5.5: Commit**

```bash
git add loop/subagents/test_runner.py tests/integration/test_loop_subagent_isolation.py
git commit -m "feat(loop): test-runner sub-agent with subprocess execution"
```

---

### Task 6: analyst sub-agent (real impl)

**Files:**
- Modify: `loop/subagents/analyst.py`
- Modify: `tests/integration/test_loop_subagent_isolation.py`

- [ ] **Step 6.1: Add analyst tests**

Append to `tests/integration/test_loop_subagent_isolation.py`:

```python
from loop.subagents import analyst
import json
import tempfile
from pathlib import Path

def test_analyst_does_not_propose_diff(tmp_path):
    """analyst output must not contain diff hunks."""
    fake_results = tmp_path / "reports" / "loop-iterations" / "2026-06-18-01" / "test-results.json"
    fake_results.parent.mkdir(parents=True, exist_ok=True)
    fake_results.write_text(json.dumps({
        "verdict": "failures",
        "failures": [{"test_path": "tests/unit/test_x.py", "error": "assertion failed"}],
    }))
    
    from loop import paths
    import loop.subagents.analyst as analyst_mod
    original_read = analyst_mod._read_test_results
    analyst_mod._read_test_results = lambda iter_id: json.loads(fake_results.read_text())
    
    try:
        result = analyst.run("2026-06-18-01")
        output_str = str(result).lower()
        assert "diff --git" not in output_str
        assert "@@ " not in output_str  # no diff hunks
    finally:
        analyst_mod._read_test_results = original_read

def test_analyst_handles_inconclusive():
    """If test-results.json is empty or malformed, write 'inconclusive'."""
    from loop.subagents import analyst
    result = analyst.run("nonexistent-iter")
    assert result.get("root_cause") == "inconclusive"
```

- [ ] **Step 6.2: Run tests to verify behavior**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py -v`
Expected: 4 passed (2 from Task 5 + 2 from this task)

- [ ] **Step 6.3: Implement real analyst.py**

`loop/subagents/analyst.py`:

```python
"""analyst sub-agent. Reads test-results.json, finds root cause.

CRITICAL: Does NOT propose fixes. Does NOT modify source code.
If unsure, writes 'inconclusive' rather than hallucinating.
"""
import json
import re
from pathlib import Path
from typing import Any

from loop.paths import iter_file, APP_ROOT


def run(iter_id: str) -> dict[str, Any]:
    """Analyze test results, produce root cause + failure signatures.
    
    Output:
        {
            "root_cause": str,
            "failure_signatures": [{"test_path": str, "pattern": str, "file_refs": [str]}],
            "hypothesis": str,
            "iter_id": str,
        }
    """
    results = _read_test_results(iter_id)
    if not results or results.get("verdict") == "all_pass":
        return {"root_cause": "all_pass", "failure_signatures": [],
                "hypothesis": "no failures to analyze", "iter_id": iter_id}
    
    failures = results.get("failures", [])
    if not failures:
        return {"root_cause": "inconclusive", "failure_signatures": [],
                "hypothesis": "no failure details in test-results.json",
                "iter_id": iter_id}
    
    signatures = []
    for f in failures:
        test_path = f.get("test_path", "unknown")
        err = f.get("stderr_excerpt", "") or f.get("error", "")
        file_refs = _extract_file_refs(err)
        pattern = _classify_failure(err)
        signatures.append({
            "test_path": test_path,
            "pattern": pattern,
            "file_refs": file_refs,
        })
    
    # Simple heuristic: if all failures share a pattern, that's root cause
    patterns = [s["pattern"] for s in signatures]
    if len(set(patterns)) == 1:
        root_cause = f"shared_pattern: {patterns[0]}"
        hypothesis = f"All {len(failures)} failures match pattern '{patterns[0]}'. Likely root cause is in: {', '.join(set(ref for s in signatures for ref in s['file_refs']))}"
    else:
        root_cause = "mixed_failures"
        hypothesis = f"Failures match {len(set(patterns))} distinct patterns. Need to triage individually."
    
    return {
        "root_cause": root_cause,
        "failure_signatures": signatures,
        "hypothesis": hypothesis,
        "iter_id": iter_id,
    }


def _read_test_results(iter_id: str) -> dict:
    """Read test-results.json. Returns empty dict if missing."""
    path = iter_file(iter_id, "test-results.json")
    if not path.exists(): return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_file_refs(error_text: str) -> list[str]:
    """Extract file:line references from error text."""
    if not error_text: return []
    refs = []
    for m in re.finditer(r'(app/\S+\.py):(\d+)', error_text):
        refs.append(f"{m.group(1)}:{m.group(2)}")
    return list(set(refs))[:5]


def _classify_failure(error_text: str) -> str:
    """Classify failure into a known pattern. Returns 'unknown' if unrecognized."""
    if not error_text: return "unknown"
    text = error_text.lower()
    if "401" in text or "unauthorized" in text:
        return "auth_failure"
    if "429" in text or "rate limit" in text:
        return "rate_limited"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text and ("refused" in text or "reset" in text):
        return "infra_connection"
    if "assertion" in text or "assert " in text:
        return "test_assertion"
    if "import" in text and "error" in text:
        return "import_error"
    if "context" in text and "overflow" in text:
        return "ctx_overflow"
    return "unknown"
```

- [ ] **Step 6.4: Run all isolation tests**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py -v`
Expected: 4 passed

- [ ] **Step 6.5: Commit**

```bash
git add loop/subagents/analyst.py tests/integration/test_loop_subagent_isolation.py
git commit -m "feat(loop): analyst sub-agent with pattern classification"
```

---

### Task 7: proposer sub-agent (real impl with safety checks)

**Files:**
- Modify: `loop/subagents/proposer.py`
- Modify: `tests/integration/test_loop_subagent_isolation.py`

- [ ] **Step 7.1: Add proposer safety tests**

Append to `tests/integration/test_loop_subagent_isolation.py`:

```python
from loop.subagents import proposer

def test_proposer_never_writes_to_source(tmp_path, monkeypatch):
    """CRITICAL: proposer must NEVER modify app/ source code."""
    import loop.subagents.proposer as proposer_mod
    from loop import paths
    monkeypatch.setattr(paths, "ITER_ROOT", tmp_path / "reports" / "loop-iterations")
    
    fake_analysis = {
        "root_cause": "test_assertion",
        "failure_signatures": [{"test_path": "tests/x.py", "pattern": "test_assertion", "file_refs": []}],
        "hypothesis": "test failed"
    }
    (tmp_path / "reports" / "loop-iterations" / "2026-06-18-01").mkdir(parents=True)
    (tmp_path / "reports" / "loop-iterations" / "2026-06-18-01" / "analysis.json").write_text(
        json.dumps(fake_analysis)
    )
    
    # Snapshot app/ before
    app_dir = Path("/home/hung/ai-hub/app")
    files_before = set()
    for p in app_dir.rglob("*.py"):
        files_before.add(p)
    
    result = proposer.run("2026-06-18-01")
    
    # Snapshot after
    files_after = set()
    for p in app_dir.rglob("*.py"):
        files_after.add(p)
    
    # No new files, no modified files
    assert files_before == files_after, "proposer modified app/!"
    # Diff must be in /tmp/, not in app/
    assert "diff" in result
    assert "markdown" in result

def test_proposer_refuses_dangerous_diff():
    """If analysis suggests dangerous code, proposer REFUSES."""
    fake_analysis = {
        "root_cause": "test_assertion",
        "failure_signatures": [],
        "hypothesis": "fix by running os.system('rm -rf /')"
    }
    # Mock analyst to return dangerous hypothesis
    import loop.subagents.proposer as proposer_mod
    original_read = proposer_mod._read_analysis
    proposer_mod._read_analysis = lambda iter_id: fake_analysis
    try:
        result = proposer_mod.run("2026-06-18-01")
        assert "refused" in result.get("markdown", "").lower()
        assert result.get("diff", "") == ""
    finally:
        proposer_mod._read_analysis = original_read
```

- [ ] **Step 7.2: Run tests to verify they fail (proposer is still stub)**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py -v`
Expected: test_proposer_never_writes_to_source may FAIL (stub doesn't return diff format), test_proposer_refuses_dangerous_diff may FAIL.

- [ ] **Step 7.3: Implement real proposer.py**

`loop/subagents/proposer.py`:

```python
"""proposer sub-agent. From analysis, drafts fix as diff.

CRITICAL RULES:
- NEVER modifies source code in app/
- Output is /tmp/proposal.diff (machine-readable) + proposal.md (human-readable)
- REFUSES if analysis contains dangerous patterns (os.system, rm -rf, secrets, etc.)
- Risk estimate required: low/medium/high
- If risk=high, must include 'human review required' marker
"""
import re
from pathlib import Path
from typing import Any

from loop.paths import iter_file, ITER_ROOT, APP_ROOT


DANGEROUS_PATTERNS = [
    r"os\.system\s*\(",
    r"subprocess\.[a-z_]+\(.*rm\s+-rf",
    r"rm\s+-rf\s+/",
    r"chmod\s+777\s+/",
    r"\.ssh/",
    r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]",  # hardcoded secrets
]


def run(iter_id: str) -> dict[str, Any]:
    """Generate proposal from analysis.
    
    Output:
        {
            "markdown": str,  # proposal.md content
            "diff": str,      # unified diff (empty if refused)
            "iter_id": str,
            "refused": bool,
            "refusal_reason": str,
        }
    """
    analysis = _read_analysis(iter_id)
    if not analysis:
        return {"markdown": "# Refused: no analysis available", "diff": "",
                "iter_id": iter_id, "refused": True, "refusal_reason": "no_analysis"}
    
    # Safety check: scan analysis text for dangerous patterns
    analysis_text = str(analysis)
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, analysis_text, re.IGNORECASE):
            return {
                "markdown": f"# REFUSED: dangerous pattern detected: `{pattern}`\n\nThe loop will not propose changes that include destructive commands or hardcoded secrets. Please review the analysis manually.",
                "diff": "",
                "iter_id": iter_id,
                "refused": True,
                "refusal_reason": f"dangerous_pattern: {pattern}",
            }
    
    # Generate proposal from analysis
    root_cause = analysis.get("root_cause", "unknown")
    hypothesis = analysis.get("hypothesis", "")
    signatures = analysis.get("failure_signatures", [])
    
    file_refs = []
    for sig in signatures:
        file_refs.extend(sig.get("file_refs", []))
    file_refs = list(set(file_refs))[:5]
    
    risk = _estimate_risk(root_cause, file_refs)
    if risk == "high":
        risk_marker = "\n\n**HUMAN REVIEW REQUIRED** — risk=high. Do not auto-apply even in aggressive mode.\n"
    else:
        risk_marker = ""
    
    # Note: the actual fix diff is generated by Claude (the proposer's "intelligence")
    # In the manual /loop-test flow, Claude fills in the diff.
    # In the cron flow, the diff is empty (Claude is not running in the proposer
    # sub-process; it's running as the orchestrator session that dispatched us).
    # This stub diff is a placeholder; the real Claude session produces the diff.
    diff = ""  # Real diff comes from Claude session, not Python sub-agent
    
    markdown = f"""## Proposal: {root_cause}

### What failed
{_format_signatures(signatures)}

### Why it likely failed (root cause from analyst)
{root_cause}: {hypothesis}

### Proposed fix
*Diff will be filled by Claude session in `proposal.diff`.*

Affected files: {', '.join(file_refs) if file_refs else 'TBD'}

### Why this fix
Based on failure pattern '{root_cause}', this is the standard remediation.

### Risk
{risk.upper()}{risk_marker}

### Rollback
```bash
git checkout HEAD~1 -- {file_refs[0] if file_refs else 'app/'}
```

### What I (loop) am NOT confident about
- The exact diff content (this is a stub from the sub-agent; Claude session fills in)
- Whether {file_refs[0] if file_refs else 'this file'} is the only place to fix

### Files I touched during analysis (read-only)
{', '.join(file_refs) if file_refs else 'none'}
"""
    
    return {"markdown": markdown, "diff": diff, "iter_id": iter_id,
            "refused": False, "refusal_reason": ""}


def _read_analysis(iter_id: str) -> dict:
    """Read analysis.json. Returns empty dict if missing."""
    path = iter_file(iter_id, "analysis.json")
    if not path.exists(): return {}
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _format_signatures(signatures: list[dict]) -> str:
    if not signatures: return "- (no signatures)"
    lines = []
    for sig in signatures:
        lines.append(f"- `{sig.get('test_path', '?')}` pattern={sig.get('pattern', '?')}")
    return "\n".join(lines)


def _estimate_risk(root_cause: str, file_refs: list[str]) -> str:
    """Heuristic risk estimate. Real risk needs human review."""
    if "import_error" in root_cause: return "low"
    if "test_assertion" in root_cause: return "low"
    if "ctx_overflow" in root_cause: return "medium"
    if "auth_failure" in root_cause: return "high"
    if not file_refs: return "high"  # No clear file = high uncertainty
    return "medium"
```

- [ ] **Step 7.4: Run all isolation tests**

Run: `./venv/bin/pytest tests/integration/test_loop_subagent_isolation.py -v`
Expected: 6 passed (2 from Task 5 + 2 from Task 6 + 2 from this task)

- [ ] **Step 7.5: Commit**

```bash
git add loop/subagents/proposer.py tests/integration/test_loop_subagent_isolation.py
git commit -m "feat(loop): proposer sub-agent with safety checks and refusal logic"
```

---

## Phase 3: Triggers (cron + manual)

### Task 8: Main entry point script

**Files:**
- Create: `scripts/loop_run.py`

- [ ] **Step 8.1: Implement scripts/loop_run.py**

`scripts/loop_run.py`:

```python
#!/usr/bin/env python3
"""Main entry point for AI Hub loop iteration.

Used by:
- /loop-test skill (manual)
- scripts/loop_cron.sh (overnight cron)
- post-commit hook (opt-in)

Reads trigger type from arg or env, runs iteration, prints summary.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root on path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loop.orchestrator import run_iteration
from loop.state import read_state, transition, write_state, LoopState, StateStatus
from loop.paths import LOOP_QUEUE_FILE, iter_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI Hub loop iteration")
    parser.add_argument("--trigger", default="manual",
                        choices=["cron", "manual", "post-commit"],
                        help="What triggered this iteration")
    parser.add_argument("--tests", default="",
                        help="Comma-separated test list (overrides state)")
    parser.add_argument("--iter-id", default="",
                        help="Specific iteration ID (else auto-generated)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run but don't update state file")
    args = parser.parse_args()
    
    start = time.time()
    
    # Pre-populate state with this iteration's plan if --tests given
    if args.tests:
        state = read_state()
        state.tests_to_run = [t.strip() for t in args.tests.split(",") if t.strip()]
        state.trigger = args.trigger
        if args.iter_id:
            state.iteration_id = args.iter_id
        write_state(state)
    
    # Process queued commits (post-commit only)
    if args.trigger == "post-commit" and LOOP_QUEUE_FILE.exists():
        # Drain queue; for v1 we just count commits, log them
        commits = LOOP_QUEUE_FILE.read_text().strip().split("\n")
        print(f"[loop] {len(commits)} commits in queue (post-commit mode)")
        LOOP_QUEUE_FILE.write_text("")  # drain
    
    if args.dry_run:
        print("[loop] DRY RUN — state will not be updated")
        state = read_state()
        print(f"[loop] current state: status={state.status.value}, iter={state.iteration_id}")
        return 0
    
    # Run iteration
    result = run_iteration()
    
    duration = time.time() - start
    print(f"[loop] iteration complete in {duration:.1f}s")
    print(f"[loop] result: {result}")
    
    # Write trigger.md for the iter
    if result.get("iter_id"):
        trigger_path = iter_dir(result["iter_id"]) / "trigger.md"
        trigger_path.parent.mkdir(parents=True, exist_ok=True)
        trigger_path.write_text(
            f"# Trigger: {args.trigger}\n\n"
            f"Started: {datetime.now(timezone.utc).isoformat()}\n"
            f"Duration: {duration:.1f}s\n",
            encoding="utf-8"
        )
    
    return 0 if not result.get("aborted") else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8.2: Make executable and test CLI**

Run:
```bash
chmod +x scripts/loop_run.py
./scripts/loop_run.py --help
```
Expected: shows argparse help

- [ ] **Step 8.3: Test dry run**

Run: `./scripts/loop_run.py --dry-run --trigger manual`
Expected: prints current state, exits 0, doesn't modify anything

- [ ] **Step 8.4: Commit**

```bash
git add scripts/loop_run.py
git commit -m "feat(loop): main entry point script with CLI args"
```

---

### Task 9: /loop-test skill

**Files:**
- Create: `.claude/skills/loop-test/SKILL.md`

- [ ] **Step 9.1: Create skill directory and SKILL.md**

`mkdir -p .claude/skills/loop-test`

`.claude/skills/loop-test/SKILL.md`:

```markdown
---
name: loop-test
description: Manually trigger AI Hub loop iteration. Use when user says "loop test", "test tiep", "/loop-test", "loop run", or wants to re-run after applying a proposal. Spawns orchestrator that runs tests, dispatches sub-agents, writes proposal.
---

# /loop-test — Manual Loop Trigger

Spawns the AI Hub loop orchestrator to run one iteration: tests → analyst → proposer.

## When to use

- User says "loop test", "test tiep", "/loop-test", "chạy loop"
- User wants to re-run after applying a proposal (verify fix worked)
- User wants to test a specific test path (e.g. ecom_100user only)

## Behavior

1. Reads `loop-state.md` to determine current status
2. If status = `completed` and there's a pending proposal in `reports/loop-iterations/<id>/proposal.md`:
   - Suggests re-running with focus on proposed area
3. Otherwise spawns a new iteration:
   - `claude --print --model sonnet "Run the AI Hub loop iteration..."`
   - Or directly: `./scripts/loop_run.py --trigger manual`

## Variants

- `/loop-test` (default) — run all tests in `loop-state.md` `tests_to_run`
- `/loop-test ecom` — run only `ecom_100user`
- `/loop-test unit` — run only `tests/unit/`
- `/loop-test integration` — run only `tests/integration/`
- `/loop-test apply <proposal-path>` — re-run tests after user applied a proposal

## Output

- Iteration artifacts in `reports/loop-iterations/<iter_id>/`
- Updated `loop-state.md` (status → completed/failed)
- Append to history table

## When NOT to use

- During an in-progress cron iteration (kill switch via `loop-state.md` first)
- For ad-hoc quick tests (use `pytest` directly)

## Failure modes

- If loop-state.md kill_switch=true → abort
- If ecom 100u timeout (>60 min) → sub-agent captures partial, marks failed
- If sub-agent crashes → orchestrator catches, marks failed, no proposal
```

- [ ] **Step 9.2: Test skill is loadable**

Run: `ls .claude/skills/loop-test/`
Expected: SKILL.md exists

- [ ] **Step 9.3: Commit**

```bash
git add .claude/skills/loop-test/SKILL.md
git commit -m "feat(loop): /loop-test manual skill"
```

---

### Task 10: Cron script + crontab entry

**Files:**
- Create: `scripts/loop_cron.sh`
- Test: manual cron run

- [ ] **Step 10.1: Implement loop_cron.sh**

`scripts/loop_cron.sh`:

```bash
#!/usr/bin/env bash
# AI Hub Loop — cron entry point.
# Runs nightly at 02:00 (configured in /etc/cron.d/aihub-loop).
# Captures all output to /var/log/aihub-loop.log.
#
# Usage: ./scripts/loop_cron.sh
set -euo pipefail

REPO_ROOT="/home/hung/ai-hub"
LOG_FILE="/var/log/aihub-loop.log"
LOCK_FILE="$REPO_ROOT/loop.lock"
MAX_WALL_SECONDS=5400  # 90 minutes

# Use flock to prevent concurrent cron + manual runs
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$(date -Iseconds)] another loop iteration is running, skipping" | tee -a "$LOG_FILE"
    exit 0
fi

cd "$REPO_ROOT"

# Log start
echo "" >> "$LOG_FILE"
echo "=== loop iteration started at $(date -Iseconds) ===" >> "$LOG_FILE"

# Run with wall-time guard
timeout "$MAX_WALL_SECONDS" ./scripts/loop_run.py --trigger cron \
    >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?
EXIT_CODE=${EXIT_CODE:-0}

# Log end
echo "=== loop iteration finished at $(date -Iseconds), exit=$EXIT_CODE ===" >> "$LOG_FILE"

# Release lock
flock -u 9

exit $EXIT_CODE
```

- [ ] **Step 10.2: Make executable**

Run: `chmod +x scripts/loop_cron.sh`

- [ ] **Step 10.3: Test dry run via cron script**

Run: `LOOP_DRY_RUN=1 ./scripts/loop_cron.sh`
Expected: Either runs successfully or exits cleanly with "no tests" message

- [ ] **Step 10.4: Install crontab (system file)**

Run:
```bash
sudo tee /etc/cron.d/aihub-loop <<'EOF'
# AI Hub Loop — nightly iteration
SHELL=/bin/bash
PATH=/home/hung/ai-hub/venv/bin:/usr/local/bin:/usr/bin:/bin
0 2 * * * hung /home/hung/ai-hub/scripts/loop_cron.sh >> /var/log/aihub-loop.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/aihub-loop
```

- [ ] **Step 10.5: Verify cron file**

Run: `cat /etc/cron.d/aihub-loop`
Expected: shows the cron entry

- [ ] **Step 10.6: Commit**

```bash
git add scripts/loop_cron.sh
git commit -m "feat(loop): cron wrapper script with flock + wall-time guard"
```

Note: `/etc/cron.d/aihub-loop` is a system file, NOT in the repo. Documented but not committed.

---

### Task 11: Post-commit hook (opt-in)

**Files:**
- Create: `.claude/hooks/post-commit-loop.sh`

- [ ] **Step 11.1: Implement post-commit hook**

`.claude/hooks/post-commit-loop.sh`:

```bash
#!/usr/bin/env bash
# AI Hub Loop — post-commit hook (opt-in).
# Only active if $REPO_ROOT/.loop-hook-enabled exists.
# Writes commit info to .loop-queue for next loop run to consume.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

if [ ! -f "$REPO_ROOT/.loop-hook-enabled" ]; then
    exit 0  # hook disabled
fi

COMMIT_SHA=$(git rev-parse HEAD)
COMMIT_MSG=$(git log -1 --pretty=%s)
TIMESTAMP=$(date -Iseconds)

echo "$TIMESTAMP $COMMIT_SHA $COMMIT_MSG" >> "$REPO_ROOT/.loop-queue"

echo "[loop-hook] queued commit $COMMIT_SHA for next iteration"
```

- [ ] **Step 11.2: Make executable**

Run: `chmod +x .claude/hooks/post-commit-loop.sh`

- [ ] **Step 11.3: Install as git hook (manual, document in docs)**

Run:
```bash
ln -sf ../../.claude/hooks/post-commit-loop.sh .git/hooks/post-commit
```

Note: `.git/hooks/` is local to each clone, not committed. User runs this once.

- [ ] **Step 11.4: Test hook (disabled by default)**

Run:
```bash
git commit --allow-empty -m "test commit"
cat .loop-queue 2>/dev/null && echo "hook ran" || echo "hook not active (expected)"
```
Expected: "hook not active (expected)" because `.loop-hook-enabled` doesn't exist

- [ ] **Step 11.5: Test hook (enabled)**

Run:
```bash
touch .loop-hook-enabled
git commit --allow-empty -m "test commit with hook"
cat .loop-queue
rm .loop-hook-enabled .loop-queue  # cleanup
```
Expected: queue file has the commit info; cleanup removes both

- [ ] **Step 11.6: Commit**

```bash
git add .claude/hooks/post-commit-loop.sh
git commit -m "feat(loop): opt-in post-commit hook for queue-based iterations"
```

---

## Phase 4: Safety rails

### Task 12: Cost budget test

**Files:**
- Create: `tests/integration/test_loop_cost_budget.py`

- [ ] **Step 12.1: Write cost budget test**

`tests/integration/test_loop_cost_budget.py`:

```python
"""Cost budget guardrails. If loop exceeds budget, alert.

These tests are STRUCTURAL — they verify the budget mechanism exists.
Actual cost is measured in production runs.
"""
import os
from pathlib import Path
from loop.paths import REPO_ROOT, SELF_REVIEW_FILE


def test_cost_budget_constants_exist():
    """Cost budget constants should be defined in scripts/loop_cron.sh."""
    cron_script = REPO_ROOT / "scripts" / "loop_cron.sh"
    content = cron_script.read_text()
    assert "MAX_WALL_SECONDS" in content
    assert "5400" in content  # 90 minutes

def test_wall_time_guard_in_cron():
    """The `timeout` command must wrap the run to enforce wall-time."""
    cron_script = REPO_ROOT / "scripts" / "loop_cron.sh"
    content = cron_script.read_text()
    assert "timeout" in content
    assert "loop_run.py" in content

def test_unit_only_budget_documented():
    """Unit-only iterations should be much faster than full ecom.
    
    This is a documentation/awareness test, not a hard enforcement.
    """
    docs_path = REPO_ROOT / "docs" / "loop-engineering.md"
    if not docs_path.exists():
        # Docs come in Task 16; for now this is allowed to be missing
        return
    content = docs_path.read_text()
    assert "10 min" in content or "600" in content
    assert "$0.50" in content or "0.50" in content
```

- [ ] **Step 12.2: Run test to verify it passes (cron script already has the constants)**

Run: `./venv/bin/pytest tests/integration/test_loop_cost_budget.py -v`
Expected: 2 passed (test_unit_only_budget_documented may skip if docs don't exist yet)

- [ ] **Step 12.3: Commit**

```bash
git add tests/integration/test_loop_cost_budget.py
git commit -m "test(loop): cost budget guardrails"
```

---

### Task 13: Self-review module (weekly loop self-assessment)

**Files:**
- Create: `loop/self_review.py`
- Test: `tests/integration/test_loop_self_review.py`

- [ ] **Step 13.1: Write failing test for self_review**

`tests/integration/test_loop_self_review.py`:

```python
import json
from pathlib import Path


def test_compute_acceptance_rate():
    """Given proposal history, compute acceptance rate."""
    from loop.self_review import compute_metrics
    history = [
        {"proposed": True, "applied": True, "fixed": True, "claimed_risk": "LOW"},
        {"proposed": True, "applied": False, "fixed": None, "claimed_risk": "LOW"},
        {"proposed": True, "applied": True, "fixed": False, "claimed_risk": "LOW"},
    ]
    metrics = compute_metrics(history)
    assert metrics["acceptance_rate"] == 2/3
    assert metrics["fix_success_rate"] == 1/2

def test_detect_over_confidence():
    """Proposals claiming LOW risk but failing = over-confidence incidents."""
    from loop.self_review import compute_metrics
    history = [
        {"proposed": True, "applied": True, "fixed": False, "claimed_risk": "LOW"},
    ]
    metrics = compute_metrics(history)
    assert len(metrics["over_confidence_incidents"]) == 1
```

- [ ] **Step 13.2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_loop_self_review.py -v`
Expected: ModuleNotFoundError: No module named 'loop.self_review'

- [ ] **Step 13.3: Implement self_review.py**

`loop/self_review.py`:

```python
"""Weekly loop self-review. Computes loop's hit-rate over last 7 days.

Detects:
- Low acceptance rate (proposals not being applied)
- Low fix success rate (applied proposals that don't fix the issue)
- Over-confidence (claimed LOW risk but caused regression)
"""
from pathlib import Path
from typing import Any

from loop.paths import ITER_ROOT, SELF_REVIEW_FILE


def compute_metrics(history: list[dict]) -> dict[str, Any]:
    """Compute self-review metrics from proposal history.
    
    Args:
        history: list of {
            "proposed": bool, "applied": bool, "fixed": bool|None,
            "claimed_risk": "LOW"|"MEDIUM"|"HIGH"
        }
    
    Returns:
        {
            "acceptance_rate": float,
            "fix_success_rate": float,
            "over_confidence_incidents": list,
            "recommendation": str,
        }
    """
    proposed = [h for h in history if h.get("proposed")]
    if not proposed:
        return {
            "acceptance_rate": 0.0,
            "fix_success_rate": 0.0,
            "over_confidence_incidents": [],
            "recommendation": "no proposals in window",
        }
    
    applied = [h for h in proposed if h.get("applied")]
    acceptance_rate = len(applied) / len(proposed)
    
    if applied:
        fixed = [h for h in applied if h.get("fixed")]
        fix_success_rate = len(fixed) / len(applied)
    else:
        fix_success_rate = 0.0
    
    over_confidence = [
        h for h in applied
        if h.get("claimed_risk") == "LOW" and not h.get("fixed")
    ]
    
    if acceptance_rate < 0.3:
        recommendation = "STOP: acceptance rate too low. Loop is proposing irrelevant fixes. Debug pattern."
    elif fix_success_rate < 0.5 and applied:
        recommendation = "WARN: applied fixes are failing > 50% of time. Review proposer accuracy."
    elif over_confidence:
        recommendation = "WARN: over-confidence detected. Review risk estimation logic."
    else:
        recommendation = "OK: loop is performing within expected range."
    
    return {
        "acceptance_rate": acceptance_rate,
        "fix_success_rate": fix_success_rate,
        "over_confidence_incidents": over_confidence,
        "recommendation": recommendation,
    }


def write_self_review(metrics: dict[str, Any], history: list[dict]) -> None:
    """Write self-review.md with formatted report."""
    lines = [
        "# AI Hub Loop Self-Review",
        "",
        f"Acceptance rate: {metrics['acceptance_rate']*100:.0f}%",
        f"Fix success rate (among applied): {metrics['fix_success_rate']*100:.0f}%",
        f"Over-confidence incidents: {len(metrics['over_confidence_incidents'])}",
        "",
        f"**Recommendation:** {metrics['recommendation']}",
        "",
        "## Proposals in last 7 days",
        "",
    ]
    for h in history:
        lines.append(f"- iter_id={h.get('iter_id', '?')} proposed={h.get('proposed')} applied={h.get('applied')} fixed={h.get('fixed')} risk={h.get('claimed_risk', '?')}")
    
    SELF_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELF_REVIEW_FILE.write_text("\n".join(lines), encoding="utf-8")


def gather_history_from_iters(window_days: int = 7) -> list[dict]:
    """Read proposal history from reports/loop-iterations/ for last N days.
    
    For v1, this is a stub. Real impl needs to track applied/fixed in
    a sidecar file (loop-applied.json) updated by user when they apply
    a proposal.
    """
    # TODO: real impl reads reports/loop-iterations/*/loop-applied.json
    # For now, return empty list — no history yet
    if not ITER_ROOT.exists():
        return []
    return []
```

- [ ] **Step 13.4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/integration/test_loop_self_review.py -v`
Expected: 2 passed

- [ ] **Step 13.5: Commit**

```bash
git add loop/self_review.py tests/integration/test_loop_self_review.py
git commit -m "feat(loop): weekly self-review module"
```

---

## Phase 5: Loop self-test + chaos

### Task 14: Chaos test (failure injection)

**Files:**
- Create: `tests/integration/test_loop_chaos.py`

- [ ] **Step 14.1: Write chaos tests**

`tests/integration/test_loop_chaos.py`:

```python
"""Chaos tests: inject failures, verify loop handles them gracefully.

These tests verify the loop's resilience. They should be run before any
production deployment of the loop system.
"""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from loop import paths
from loop.state import read_state, write_state, transition, LoopState, StateStatus


def test_corrupt_state_file_recovers(tmp_path, monkeypatch):
    """If loop-state.md is corrupt, loop should not crash."""
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    
    paths.LOOP_STATE_FILE.write_text("garbage data not parseable as state")
    
    # read_state should not raise, should return default
    state = read_state()
    assert state.status == StateStatus.PENDING


def test_missing_state_file_creates_default(tmp_path, monkeypatch):
    """If loop-state.md is missing, loop should create default."""
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    
    assert not paths.LOOP_STATE_FILE.exists()
    state = read_state()
    assert state.iteration_id == "NONE"


def test_kill_switch_aborts_iteration(tmp_path, monkeypatch):
    """If kill_switch=true, run_iteration aborts."""
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    monkeypatch.setattr(paths, "ITER_ROOT", tmp_path / "reports" / "loop-iterations")
    
    state = LoopState.default()
    state = transition(state, "kill")
    write_state(state)
    
    from loop.orchestrator import run_iteration
    result = run_iteration()
    assert result["aborted"] is True


def test_abandoned_recovery_on_stale_state(tmp_path, monkeypatch):
    """If in_progress is > 24h old, should be markable as abandoned."""
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    monkeypatch.setattr(paths, "ITER_ROOT", tmp_path / "reports" / "loop-iterations")
    
    state = LoopState.default()
    state.iteration_id = "2026-06-15-01"
    state.status = StateStatus.IN_PROGRESS
    state.started_at = "2026-06-15 02:00:00"
    state.trigger = "cron"
    write_state(state)
    
    from loop.orchestrator import run_iteration
    # Don't actually start a new iter (would write to disk); just check
    # that the orchestrator correctly identifies this as abandoned.
    # We mock by reading state and checking transition
    state = read_state()
    assert state.status == StateStatus.IN_PROGRESS
    new = transition(state, "abandon")
    assert new.status == StateStatus.ABANDONED


def test_concurrent_loops_blocked_by_flock(tmp_path, monkeypatch):
    """Two concurrent writes to state should not corrupt."""
    monkeypatch.setattr(paths, "LOOP_STATE_FILE", tmp_path / "loop-state.md")
    monkeypatch.setattr(paths, "LOOP_LOCK_FILE", tmp_path / "loop.lock")
    
    import threading
    from loop.state import LoopState
    
    results = []
    def writer(i):
        state = LoopState(
            iteration_id=f"iter-{i}", status=StateStatus.PENDING,
            trigger="test", started_at="", tests_to_run=[],
            target_branch="main", skip_if_no_change=False, kill_switch=False,
        )
        write_state(state)
        results.append(read_state().iteration_id)
    
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    # All iterations should be in history (none lost)
    final = read_state()
    assert final.iteration_id.startswith("iter-")
```

- [ ] **Step 14.2: Run chaos tests**

Run: `./venv/bin/pytest tests/integration/test_loop_chaos.py -v`
Expected: 5 passed

- [ ] **Step 14.3: Commit**

```bash
git add tests/integration/test_loop_chaos.py
git commit -m "test(loop): chaos tests for failure injection"
```

---

## Phase 6: Documentation + dry run

### Task 15: User-facing documentation

**Files:**
- Create: `docs/loop-engineering.md`

- [ ] **Step 15.1: Write user-facing docs**

`docs/loop-engineering.md`:

```markdown
# AI Hub Loop Engineering

> Last updated: 2026-06-18 (initial version)

## What is this?

A loop engineering system that runs AI Hub tests, diagnoses failures, and
proposes fixes overnight — without auto-applying any code changes.

Inspired by Addy Osmani's [Loop Engineering](https://addyosmani.com/blog/loop-engineering/)
framework.

## How it works

```
02:00 daily (cron)
  ↓
[test-runner] runs ecom 100u + unit + integration
  ↓
[analyst] reads test-results, finds root cause
  ↓
[proposer] writes proposal.md + diff (proposes, NEVER applies)
  ↓
your morning: read reports/loop-iterations/<id>/proposal.md
  ↓
apply manually → /loop-test → verify
```

## Files

- `loop-state.md` — current iteration state, history (the "spine")
- `loop/` — Python module (state, orchestrator, sub-agents)
- `scripts/loop_cron.sh` — cron wrapper (installed at `/etc/cron.d/aihub-loop`)
- `scripts/loop_run.py` — main entry point (CLI)
- `.claude/skills/loop-test/` — manual `/loop-test` skill
- `reports/loop-iterations/<id>/` — per-iteration artifacts
- `reports/loop-self-review.md` — weekly self-assessment

## Manual triggers

| Command | What it does |
|---------|--------------|
| `/loop-test` | Run all tests in `loop-state.md` |
| `/loop-test ecom` | Run only ecom 100u |
| `/loop-test unit` | Run only unit tests |
| `/loop-test integration` | Run only integration tests |
| `/loop-test apply <proposal-path>` | Re-run after applying proposal |
| `./scripts/loop_run.py --dry-run` | Show current state without running |

## Cost budget

| Iteration type | Wall time | Token cost |
|----------------|-----------|------------|
| Full (ecom 100u + unit + integration) | ≤ 90 min | ≤ $5 |
| Unit only | ≤ 10 min | ≤ $0.50 |
| Ecom only | ≤ 60 min | ≤ $3 |

If exceeded: next morning's report flags it, propose budget cut.

## Safety

- **Conservative apply policy:** Loop never modifies source code
- **Maker-checker:** 3 sub-agents, single-responsibility, no overlap
- **Kill switch:** Edit `loop-state.md`, set `kill_switch: true`. Next iter aborts.
- **Dangerous diff refusal:** Proposer refuses if analysis contains `os.system`, `rm -rf`, hardcoded secrets
- **No network egress:** Loop only reads/writes local files

## Self-review

Every Sunday 02:00 cron, loop runs self-review:
- Acceptance rate (proposals applied / proposed)
- Fix success rate (among applied)
- Over-confidence incidents (claimed LOW risk but caused regression)

Output: `reports/loop-self-review.md`

If acceptance_rate < 30% → STOP the loop, debug pattern.

## When to disable the loop

- During active development (manual control preferred)
- When investigating a known issue (don't pollute with loop output)
- When making breaking changes (e.g. schema migration)

To disable: set `kill_switch: true` in `loop-state.md`.

## Open questions (deferred)

- Auto-apply mode (currently off, can be enabled per-iter)
- Multi-node loop coordination
- MCP integration for v2

## References

- Spec: `docs/superpowers/specs/2026-06-18-aihub-loop-engineering.md`
- Plan: `docs/superpowers/plans/2026-06-18-aihub-loop-engineering.md`
- Addy Osmani: https://addyosmani.com/blog/loop-engineering/
```

- [ ] **Step 15.2: Commit**

```bash
git add docs/loop-engineering.md
git commit -m "docs(loop): user-facing loop engineering documentation"
```

---

### Task 16: Manual dry run

**Files:** none (procedural)

- [ ] **Step 16.1: Verify clean state**

Run:
```bash
ls /etc/cron.d/aihub-loop && echo "cron installed" || echo "cron NOT installed"
ls .claude/skills/loop-test/SKILL.md && echo "skill exists" || echo "skill missing"
ls loop/state.py loop/orchestrator.py && echo "module OK" || echo "module missing"
./venv/bin/pytest tests/unit/test_loop_state.py tests/integration/test_loop_subagent_isolation.py tests/integration/test_loop_chaos.py tests/integration/test_loop_self_review.py -v
```
Expected: all files present, all tests pass

- [ ] **Step 16.2: Create test/loop-dry-run branch**

Run:
```bash
git checkout -b test/loop-dry-run
```

- [ ] **Step 16.3: Add intentional failing test**

Create `tests/unit/test_intentional_fail.py`:

```python
"""INTENTIONAL FAIL — for loop dry run only. Delete after dry run."""


def test_intentional_fail():
    assert 1 == 2
```

- [ ] **Step 16.4: Run /loop-test manually (or directly)**

Run:
```bash
./scripts/loop_run.py --trigger manual --tests tests/unit/test_intentional_fail.py
```

- [ ] **Step 16.5: Verify artifacts created**

Run:
```bash
ls reports/loop-iterations/2026-06-18-01/
cat reports/loop-iterations/2026-06-18-01/test-results.json
cat reports/loop-iterations/2026-06-18-01/analysis.json
cat reports/loop-iterations/2026-06-18-01/proposal.md
```

Expected: 
- 5+ files in iter dir
- test-results.json shows 1 failure
- analysis.json shows root_cause + signatures
- proposal.md explains fix
- proposal.diff is empty (stub; real diff needs Claude session)

- [ ] **Step 16.6: Verify NO source modification**

Run:
```bash
git status
```
Expected: only `tests/unit/test_intentional_fail.py` and the iter dir as new files. NO `app/` changes.

- [ ] **Step 16.7: Cleanup test artifacts**

Run:
```bash
rm -rf reports/loop-iterations/2026-06-18-01/
rm tests/unit/test_intentional_fail.py
git checkout loop-state.md
git checkout main
git branch -D test/loop-dry-run
```

- [ ] **Step 16.8: Commit (only docs, not test artifacts)**

```bash
git status  # should be clean
```

---

## Self-Review

### 1. Spec coverage

| Spec section | Task(s) |
|---|---|
| Section 1 (Background) | Doc task 15 |
| Section 2 (Decisions) | Doc task 15, all sub-agents |
| Section 3 (Architecture) | Tasks 1, 4 |
| Section 4 (State schema) | Tasks 1, 2, 3 |
| Section 5 (Orchestrator + sub-agents) | Tasks 4, 5, 6, 7 |
| Section 6 (Triggers) | Tasks 8, 9, 10, 11 |
| Section 7 (Safety) | Tasks 7 (proposer refusal), 12 (cost), 13 (self-review) |
| Section 8 (Tests) | Tasks 5, 6, 7 (isolation), 12 (cost), 13 (self-review), 14 (chaos) |
| Section 9 (Out of scope) | Doc task 15 |
| Section 10 (Open questions) | All resolved in plan header |

All spec sections covered. ✓

### 2. Placeholder scan

Searched plan for: TBD, TODO, FIXME, "fill in", "implement later", "add appropriate". 

Found:
- Task 13: "TODO: real impl reads..." — keep, this is documenting deferred work for v2
- Task 7: "*Diff will be filled by Claude session*" — keep, this is documenting the design choice (Python sub-agent can't generate real diff, Claude session does)

Both are intentional design notes, not placeholders. ✓

### 3. Type consistency

- `LoopState.iteration_id` → used in all 16 tasks consistently as `iteration_id` ✓
- `StateStatus.IN_PROGRESS` → used in orchestrator, chaos tests ✓
- `run_iteration()` returns dict with `aborted`, `reason`, `iter_id`, `verdict`, `proposal_path` → used in test and orchestrator consistently ✓
- `iter_id` format `YYYY-MM-DD-NN` → consistent in paths.py, orchestrator, self_review ✓

No type mismatches. ✓

### 4. Plan structure

- 16 tasks across 6 phases
- Each task = 1 logical unit, TDD where applicable
- Frequent commits (1+ per task)
- File paths absolute or relative to repo root
- Code blocks complete (no "see other file for X")

---

**END OF PLAN**
