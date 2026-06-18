# AI Hub Loop Engineering — Design

> **Date:** 2026-06-18
> **Author:** brainstorm session (user + Claude)
> **Status:** Design approved, awaiting implementation plan
> **Source of inspiration:** Addy Osmani — "Loop Engineering" (addyosmani.com/blog/loop-engineering/)
> **Target repo:** `/home/hung/ai-hub` (FastAPI gateway, llama.cpp 12B Q4 + E4B + reranker)

---

## 1. Background & Motivation

### Problem statement

AI Hub's test/development cycle today is **operator-driven**:

- User manually runs `pytest tests/integration/test_ecommerce_100users.py`
- User manually reads JSON report
- User manually diagnoses failures (e.g. "order_lookup went 0% → 80%")
- User manually proposes + applies fixes
- User manually re-runs to verify

This works for low-frequency changes but breaks down when:
- Overnight, code is committed but not tested
- Multiple failure modes surface simultaneously (4 success criteria in ecom)
- A user iteration takes 25-60 minutes (ecom 100u) — too slow to be interactive
- The same fix pattern recurs (e.g. "ctx too small", "prompt missing system message")

### Loop Engineering concept (from Addy Osmani)

> "You don't really need to be good at prompting anymore. The thing to get good at is the loop that does the prompting for you." — Peter Steinberger

Addy's framework has 5 building blocks + 1 cross-cutting concern:

| # | Block | Addy's role | This design's instantiation |
|---|-------|-------------|---------------------------|
| 1 | Automations | Heartbeat — scheduled trigger | cron @ 02:00 + manual `/loop-test` |
| 2 | Worktrees | Parallel feature isolation | (deferred — loop proposes only, user applies) |
| 3 | Skills | Project knowledge externalized | `/loop-test` skill (new) + 8 existing sub-agents |
| 4 | Connectors | Real tool integration | local filesystem only (no MCP for v1) |
| 5 | Sub-agents | Maker vs checker | 3 sub-agents: test-runner, analyst, proposer |
| 6 | State/Memory | Spine across iterations | `loop-state.md` + per-iter directory |

### Three caveats Addy warns about (defense in section 5)

1. **Verification vẫn ở người** — unattended loop = unattended mistakes
2. **Comprehension debt** — ship code bạn không đọc = gap giữa "code tồn tại" và "bạn hiểu"
3. **Cognitive surrender** — loop chạy ngon → bạn ngưng có opinion

---

## 2. Design Decisions (confirmed with user)

| Question | Choice | Rationale |
|----------|--------|-----------|
| Loop scope | **Full dev loop** (test → propose fix → re-test) | User wants highest leverage |
| Trigger | **Daily cron @ 02:00 + manual `/loop-test`** | Overnight non-interrupting + manual override |
| Apply policy | **Conservative — propose only** | Per Addy caveat #1, safer for unattended |
| Approach | **C — Multi-agent loop with state file** | Best fit for Addy's framework, sub-agent isolation |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  AI Hub Loop Engineering                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  TRIGGER LAYER                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ cron @ 02:00 │  │ /loop-test   │  │ post-commit  │         │
│  │ (overnight)  │  │ (manual)     │  │ hook (opt)   │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         └─────────────────┼─────────────────┘                  │
│                           ▼                                     │
│  ORCHESTRATOR (Claude session)                                  │
│  ┌─────────────────────────────────────────────┐               │
│  │ 1. Read loop-state.md → next test to run    │               │
│  │ 2. Dispatch sub-agents:                     │               │
│  │    a. test-runner (runs tests)              │               │
│  │    b. analyst (reads results, root cause)   │               │
│  │    c. proposer (drafts fix as diff)         │               │
│  │ 3. Update loop-state.md                     │               │
│  │ 4. Write reports/loop-iterations/.../       │               │
│  │ 5. Exit                                     │               │
│  └─────────────────────────────────────────────┘               │
│         │                                                       │
│         ▼                                                       │
│  STATE LAYER (filesystem)                                       │
│  ┌─────────────────────────────────────────────┐               │
│  │ loop-state.md         current iter, history │               │
│  │ reports/loop-iters/   per-iter artifacts     │               │
│  │ memory/               cross-session learnings│               │
│  └─────────────────────────────────────────────┘               │
│         │                                                       │
│         ▼                                                       │
│  HUMAN REVIEW (your morning)                                    │
│  - Read proposal.md                                             │
│  - Apply diff manually OR delegate to /loop-test                │
│  - /loop-test re-runs failed tests after apply                  │
└─────────────────────────────────────────────────────────────────┘
```

### Key principles

1. **State file = spine** — orchestrator state, next-action, history
2. **Sub-agents = maker vs checker** — each does 1 thing, focused context
3. **No code mutation by loop** — propose only
4. **Idempotent** — re-running /loop-test picks up from loop-state.md
5. **Audit trail** — every iteration's artifacts in `reports/loop-iterations/`

---

## 4. State Schema

### File 1: `loop-state.md` (root, single source of truth)

Frontmatter + structured sections:

```markdown
---
# AI Hub Loop State
# Last updated: 2026-06-18 02:00:00 by orchestrator
# This file is the spine of the loop — every iteration reads/writes it.
---

## Current iteration
- iteration_id: 2026-06-18-02
- started_at: 2026-06-18 02:00:00
- trigger: cron
- status: in_progress    # pending | in_progress | completed | failed | abandoned

## Iteration plan
- tests_to_run:
    - ecom_100user (priority=1, timeout=3600s)
    - tests/integration/test_chat_endpoint.py (priority=2, timeout=300s)
- target_branch: main
- skip_if_no_change: true

## Test results (filled by test-runner sub-agent)
- ecom_100user: NOT_RUN
- test_chat_endpoint: NOT_RUN

## Analysis (filled by analyst sub-agent)
- root_cause: ""
- failure_signatures: []

## Proposal (filled by proposer sub-agent)
- proposal_path: ""
- diff_summary: ""

## History (last 10 iterations)
| iter_id          | started          | trigger | verdict | proposal_applied |
|------------------|------------------|---------|---------|------------------|
| 2026-06-17-01    | 2026-06-17 02:00 | cron    | PASS    | n/a              |
| 2026-06-18-01    | 2026-06-18 02:00 | cron    | FAIL    | pending          |
```

### File 2: `reports/loop-iterations/<iter_id>/` (per-iter artifacts)

```
reports/loop-iterations/2026-06-18-01/
├── trigger.md          # why this iter started
├── test-results.json   # raw pytest/ecom output, structured
├── test-summary.md     # human-readable
├── analysis.md         # analyst sub-agent output
├── proposal.md         # proposer sub-agent output
└── proposal.diff       # machine-readable diff
```

### Cross-session memory

`~/.claude/projects/-home-hung-ai-hub/memory/loop_learnings_<date>.md` for cross-iteration patterns.

### State transitions

```
pending → in_progress → (completed | failed | abandoned)
                                  ↓
                          user applied proposal
                                  ↓
                          next iter: re-run failed tests
```

### Idempotency rule

Before starting, orchestrator checks `loop-state.md` status:
- If `in_progress` and last update > 24h ago → mark as `abandoned`, start fresh
- If `in_progress` and recent → resume from where left off
- If `completed` and proposal not applied → ask user (manual) or wait (cron)

---

## 5. Orchestrator + Sub-agents

### Orchestrator (main Claude session, spawned by cron or `/loop-test`)

```python
def run_loop_iteration():
    state = read_loop_state()
    iter_id = state["current_iteration"]["iteration_id"]
    ensure_iter_dir(iter_id)
    write_trigger(iter_id)

    update_status("in_progress")

    # Sub-agent 1: test-runner
    test_results = dispatch_subagent("test-runner", {
        "tests": state["iteration_plan"]["tests_to_run"],
        "iter_id": iter_id,
        "timeout_total": 3600
    })
    write_json(f"reports/loop-iterations/{iter_id}/test-results.json", test_results)

    # Decision: all pass → done
    if test_results["verdict"] == "all_pass":
        update_status("completed")
        append_history(iter_id, "PASS")
        return

    # Sub-agent 2: analyst
    analysis = dispatch_subagent("analyst", {
        "test_results_path": f"reports/loop-iterations/{iter_id}/test-results.json",
        "code_root": "/home/hung/ai-hub/app",
        "iter_id": iter_id
    })
    write_markdown(f"reports/loop-iterations/{iter_id}/analysis.md", analysis)

    # Sub-agent 3: proposer
    proposal = dispatch_subagent("proposer", {
        "analysis_path": f"reports/loop-iterations/{iter_id}/analysis.md",
        "code_root": "/home/hung/ai-hub/app",
        "iter_id": iter_id,
        "policy": "conservative"
    })
    write_markdown(f"reports/loop-iterations/{iter_id}/proposal.md", proposal)
    write_diff(f"reports/loop-iterations/{iter_id}/proposal.diff", proposal["diff"])

    update_status("completed")
    update_proposal_path(f"reports/loop-iterations/{iter_id}/proposal.md")
    append_history(iter_id, "FAIL", proposal_path=...)
```

### Sub-agents (3, single-responsibility)

#### test-runner

- **Role:** Run tests, capture output, no analysis
- **Inputs:** test list, timeout
- **Outputs:** `test-results.json` (structured), `test-summary.md` (human-readable)
- **Tools:** Bash, Read (for log inspection)
- **Failure modes:** timeout → partial results; crash → empty results with flag

#### analyst

- **Role:** Read `test-results.json`, find root cause, identify failure patterns
- **Inputs:** `test-results.json`, `app/` source (Read-only)
- **Outputs:** `analysis.md`
- **Tools:** Read, Grep, Glob
- **Failure modes:** ambiguous → write "inconclusive"; flaky → flag re-run; perf-only → flag

#### proposer

- **Role:** From `analysis.md`, write `proposal.md` with specific diff
- **Inputs:** `analysis.md`, `app/` source (Read-only)
- **Outputs:** `proposal.md` (markdown rationale), `proposal.diff` (machine-readable)
- **Tools:** Read, Grep, Glob, Edit (ONLY for building diff in `/tmp/`, not source)
- **Constraints:**
  - NEVER apply diff to source
  - Diff must be atomic (1 logical change)
  - Include rollback instructions
  - Estimate risk: low/medium/high
  - If risk=high → "human review required, do not auto-apply even in aggressive mode"

### Sub-agent isolation (per Addy caveat #2)

Each sub-agent has a focused system prompt:

```yaml
# analyst
You are a failure analyst. You receive test-results.json and read code.
You DO NOT propose fixes. You DO NOT modify files.
Your output is analysis.md: root cause, failure signatures, hypothesis.
If unsure, write "inconclusive" — do not hallucinate causes.

# proposer
Your input is analysis.md. Do not run tests. Do not apply changes.
Output diff in /tmp/, never in source.
```

---

## 6. Triggers (3 sources, 1 orchestrator entrypoint)

### Trigger 1: Cron @ 02:00 (overnight)

`/etc/cron.d/aihub-loop`:

```cron
SHELL=/bin/bash
PATH=/home/hung/ai-hub/venv/bin:/usr/local/bin:/usr/bin:/bin
0 2 * * * hung /home/hung/ai-hub/scripts/loop_cron.sh >> /var/log/aihub-loop.log 2>&1
```

`scripts/loop_cron.sh` (excerpt):

```bash
nohup claude --print \
  --model sonnet \
  --append-system-prompt "You are running the AI Hub loop iteration. Read /home/hung/ai-hub/loop-state.md, follow the orchestrator logic, exit when done." \
  "Run the AI Hub loop iteration. Read loop-state.md first, then dispatch sub-agents. Exit when iteration is complete. Do not wait for user input." \
  >> /var/log/aihub-loop.log 2>&1 &
disown
```

**Cost:** ~30-60 min wall time overnight, ~$0.50-$2 tokens per iteration.

### Trigger 2: `/loop-test` (manual skill)

`.claude/skills/loop-test/SKILL.md`:

```markdown
---
name: loop-test
description: Manually trigger AI Hub loop iteration. Use when user says "loop test", "test tiep", "/loop-test", or wants to re-run after applying a proposal.
---

# /loop-test — Manual Loop Trigger

Spawn a Claude session that reads loop-state.md and runs the orchestrator.

Variants:
- /loop-test full → run all tests in plan
- /loop-test ecom → run ecom_100u only
- /loop-test unit → run unit/ only
- /loop-test apply <proposal-path> → if user has applied diff, re-test
```

### Trigger 3: Post-commit hook (opt-in)

`.claude/hooks/post-commit-loop.sh` writes commit info to `.loop-queue` if `.loop-hook-enabled` exists. Loop checks queue at start of each iter, runs lightweight test (unit + smoke) on that commit.

**Disabled by default** — not every commit needs loop run.

### Failure handling (cron path)

| Failure | Detection | Action |
|---|---|---|
| Cron script crash | exit code != 0 | Log to `/var/log/aihub-loop.log`; next night tries again |
| Claude session hangs | wall time > 90 min | `kill -9`; mark iter `abandoned` in state |
| Sub-agent returns garbage | proposal.md empty/malformed | Mark iter `failed`; user sees raw JSON |
| Disk full | write fails | Log error, exit; alert via `security.log` |

---

## 7. Safety (the "3 caveats" defense)

### Caveat #1: Maker-checker hard barrier

Sub-agents are non-overlapping by system prompt. Verbatim instructions:

- test-runner: "Your output is raw test results. Do not interpret failures. Do not propose fixes."
- analyst: "Your input is test-results.json. Do not modify source. Do not propose diffs. Output analysis only."
- proposer: "Your input is analysis.md. Do not run tests. Do not apply changes. Output diff in /tmp/, never in source."

**Kill switch:** `loop-state.md` has `kill_switch: false`. If user sets true, next iter aborts immediately.

### Caveat #2: Comprehension debt defense — proposal.md is verbose by design

`proposal.md` template:

```markdown
## Proposal: <one-line summary>

### What failed
<paste test-results.json failure excerpt, 1:1>

### Why it likely failed (root cause from analyst)
<analysis.md excerpt, with file:line references>

### Proposed fix
<full diff, every line commented>

### Why this fix
<2-3 paragraphs of reasoning, citing analogous past fixes if any>

### Risk
LOW / MEDIUM / HIGH
- If MEDIUM/HIGH: list scenarios where this could be wrong
- Rollback: <one command>

### What I (loop) am NOT confident about
<explicit uncertainty list — the loop MUST write this section>

### Files I touched during analysis (read-only)
<file list with line counts>
```

**Goal: bạn đọc proposal.md trong 5 phút, biết loop đề xuất cái gì, tại sao, và loop không chắc chỗ nào. Nếu bạn không hiểu → KHÔNG apply → preserve comprehension.**

### Caveat #3: Cognitive surrender defense — weekly loop self-review

Sunday 02:00 cron runs an extra sub-iteration:

```
- Read last 7 days of proposals
- For each: was applied? Did applied fix pass re-test?
- Compute: proposal_acceptance_rate, fix_success_rate
- Write loop-self-review.md with:
    - "Proposals I proposed but you didn't apply"
    - "Proposals I proposed that you applied but didn't fix"
    - "Proposals where I over-confidently claimed risk=LOW but caused regression"
- Update memory/loop_learnings_*.md with cross-iter patterns
```

**Output: bạn thấy loop's hit-rate. Nếu acceptance_rate < 30% → loop đang đề xuất sai → bạn dừng loop, debug pattern.**

### Additional safety rails

- API key never logged (already enforced)
- Loop runs as user `hung` (not root) — cron file specifies user
- Loop log at `/var/log/aihub-loop.log` (separate from `security.log`)
- No network egress beyond localhost
- Proposal.diff checked for `os.system`, `subprocess`, `rm -rf`, hardcoded secrets — proposer sub-agent MUST grep and refuse if found

---

## 8. Testing the Loop Itself

### Test 1: State machine (unit) — `tests/unit/test_loop_state.py`

```python
def test_pending_to_in_progress(): ...
def test_idempotent_re_run(): ...
def test_abandoned_recovery(): ...
def test_invalid_state_recovery(): ...
```

### Test 2: Sub-agent boundaries (integration) — `tests/integration/test_loop_subagent_isolation.py`

```python
def test_test_runner_does_not_propose(): ...
def test_proposer_never_applies(): ...   # CRITICAL: source untouched
def test_proposer_refuses_dangerous_diff(): ...
```

### Test 3: End-to-end dry run (manual, 1 lần)

1. Branch `test/loop-dry-run`
2. Add `tests/unit/test_intentional_fail.py`
3. Run `/loop-test`
4. Verify: state → completed, results.json has 1 failure, analysis.md correct, source unchanged
5. Apply proposal manually, run `/loop-test` again
6. Verify: all pass, history has 2 rows

### Test 4: Loop self-review — `tests/integration/test_loop_self_review.py`

```python
def test_self_review_accuracy(): ...   # acceptance_rate, fix_success_rate, over_confidence
```

### Test 5: Failure injection (chaos test)

| Inject | Expected behavior |
|---|---|
| Kill `claude --print` mid-iter | state stays `in_progress`, next iter marks `abandoned` |
| Corrupt loop-state.md | Backs up `.bak`, starts fresh, logs warning |
| Disk full on proposal.md | Mark iter `failed`, no proposal next morning |
| Wrong API key in .env | All tests 401, analyst writes "auth failure", proposal empty |
| llama-server crashes mid-ecom | test-runner captures crash, analyst flags "infra failure" |

### Test 6: Cost budget — `tests/integration/test_loop_cost_budget.py`

```python
def test_ecom_iteration_cost_under_budget():
    assert wall_time < 3600  # 1h
    assert cost_usd < 5.0

def test_unit_only_iteration_under_budget():
    assert wall_time < 600   # 10 min
    assert cost_usd < 0.50
```

**Section 8 acceptance criteria:**

1. All 6 tests pass
2. Dry run completes in 2 manual `/loop-test` calls
3. Chaos tests document failure mode for each inject
4. Cost budget test enforces economic viability

---

## 9. Out of scope (explicit non-goals)

- **Auto-apply diff** (per user's conservative policy)
- **Multi-node loop coordination** (loop runs on 1 machine)
- **MCP integration for v1** (filesystem only)
- **Web UI for loop state** (markdown reports only)
- **Real-time monitoring** (loop is overnight or manual)
- **Loop on /explainer-channel** (TikTok use case, separate project)

---

## 10. Open questions (deferred to implementation plan)

1. **Cron daemon vs systemd timer** — cron is simpler, systemd gives better logging
2. **State file locking** — concurrent loop runs (manual + cron overlap) need flock
3. **Sub-agent implementation** — separate Claude Code agents vs sub-agent invocation in same session
4. **Proposal diff format** — unified diff vs JSON patch
5. **Memory file rotation** — when to start a new `loop_learnings_*.md`

These are implementation details, not design questions. writing-plans skill will resolve.

---

## 11. References

- Addy Osmani, "Loop Engineering" — https://addyosmani.com/blog/loop-engineering/
- Boris Cherny (Anthropic), `/loop` and `/goal` primitives in Claude Code
- AI Hub project context: `/home/hung/ai-hub/CLAUDE.md`
- Memory: `/home/hung/.claude/projects/-home-hung-ai-hub/memory/`
- E-com test history: `reports/2026-06-13-ecommerce-100user/`
- Loop precedents in this repo: `scripts/parallel_ecom_test.sh` (already implements part of the state-file pattern)

---

**END OF SPEC**
