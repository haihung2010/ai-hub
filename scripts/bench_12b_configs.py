#!/usr/bin/env python3
"""Master orchestrator: run 3 Stage A configs, then Stage B on top 1-2.

Sequential, not parallel (to avoid GPU contention).
DB snapshot/restore between every config for isolation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
REPORTS = REPO / "reports" / "bench_12b"
ERRORS_LOG = REPORTS / "errors.log"

# 3 Stage A configurations (config_name, primary_launch_script, extra_launch_scripts)
STAGE_A_CONFIGS = [
    {
        "name": "Q4-combo",
        "primary": "start_12b_q4_text.sh",
        "extras": ["start_e2b_q4_mmproj.sh"],
    },
    {
        "name": "Q6-combo",
        "primary": "start_12b_q6_text.sh",
        "extras": ["start_e2b_q4_mmproj.sh"],
    },
    {
        "name": "Q8-standalone",
        "primary": "start_12b_q8_mmproj.sh",
        "extras": [],
    },
]


def log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(ERRORS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def run_config(config: dict, stage_b: bool = False) -> dict | None:
    """Run a single config benchmark with snapshot/restore."""
    name = config["name"]
    log(f"=== Stage {'B' if stage_b else 'A'}: {name} ===")

    # 1. Snapshot DB
    ts = int(time.time())
    snap_name = f"{ts}_{name.replace('-', '_')}"
    log(f"Snapshot to /tmp/ihi_snapshots/{snap_name}")
    r = subprocess.run(["bash", str(REPO / "scripts" / "snapshot_ihi_db.sh"), snap_name],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"ERROR: snapshot failed for {name}: {r.stderr}")
        return None

    try:
        # 2. Start primary LLM
        log(f"Starting {config['primary']}")
        r = subprocess.run(["bash", str(REPO / "scripts" / config["primary"])],
                           cwd=REPO, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"ERROR: primary launch failed for {name}: {r.stderr}")
            return None

        # 3. Start extras (if any)
        for extra in config["extras"]:
            log(f"Starting {extra}")
            r = subprocess.run(["bash", str(REPO / "scripts" / extra)],
                               cwd=REPO, capture_output=True, text=True)
            if r.returncode != 0:
                log(f"WARN: extra launch {extra} failed: {r.stderr} — continuing")

        # 4. Run benchmark
        out_path = REPORTS / f"{name.lower().replace('-combo', '_combo').replace('-standalone', '_standalone')}_{'max_load' if stage_b else 'basic'}.json"
        cmd = [
            "./venv/bin/python", str(REPO / "scripts" / "bench_single_config.py"),
            "--config", name,
            "--output", str(out_path),
        ]
        if stage_b:
            cmd.append("--max-load")
        log(f"Running benchmark: {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode not in (0, 1):  # 0=ok, 1=warning, 2=fatal
            log(f"ERROR: benchmark failed (exit {r.returncode}) for {name}")
            return None

        # 5. Read result
        if out_path.exists():
            result = json.loads(out_path.read_text())
            log(f"Result for {name}: peak_tok_s={result.get('aggregate', {}).get('peak_tok_s', 0)}")
            return result
        return None

    finally:
        # 6. Restore DB
        snap_dir = f"/tmp/ihi_snapshots/{snap_name}"
        log(f"Restoring DB from {snap_dir}")
        subprocess.run(["bash", str(REPO / "scripts" / "restore_ihi_db.sh"), snap_dir],
                       cwd=REPO, capture_output=True, text=True)
        # 7. Stop all llama-servers
        log("Stopping all llama-server processes")
        subprocess.run(["pkill", "-f", "llama-server"], capture_output=True)
        time.sleep(2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage-a-only", action="store_true", help="Skip Stage B")
    p.add_argument("--configs", nargs="*", default=None, help="Subset of configs to run")
    args = p.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    log(f"Starting benchmark orchestrator. Reports dir: {REPORTS}")

    configs = STAGE_A_CONFIGS
    if args.configs:
        configs = [c for c in STAGE_A_CONFIGS if c["name"] in args.configs]

    # Stage A: all configs
    stage_a_results = []
    for config in configs:
        result = run_config(config, stage_b=False)
        if result is None:
            log(f"WARN: {config['name']} failed; continuing")
            continue
        stage_a_results.append(result)
        time.sleep(5)  # pause between configs

    if not stage_a_results:
        log("FATAL: no Stage A results; aborting")
        sys.exit(2)

    # Rank
    sys.path.insert(0, str(REPO / "scripts"))
    from bench_metrics import rank_configs
    ranked = rank_configs([{
        "name": r["config"],
        "peak_tok_s": r.get("aggregate", {}).get("peak_tok_s", 0),
        "p95_latency_at_20": r.get("aggregate", {}).get("p95_latency_at_20", 0),
        "quality": r.get("quality", 0),
    } for r in stage_a_results])

    log("Stage A ranking:")
    for i, r in enumerate(ranked, 1):
        log(f"  {i}. {r['name']} (score: {r['composite_score']:.3f})")

    # Stage B: top 1-2
    if args.stage_a_only:
        log("--stage-a-only: skipping Stage B")
    elif len(ranked) >= 1:
        top1_name = ranked[0]["name"]
        top1_config = next(c for c in STAGE_A_CONFIGS if c["name"] == top1_name)
        log(f"Stage B on winner: {top1_name}")
        run_config(top1_config, stage_b=True)

    # Generate final report
    log("Generating final report")
    subprocess.run([
        "./venv/bin/python", str(REPO / "scripts" / "gen_final_report.py"),
        "--reports-dir", str(REPORTS),
        "--output", str(REPORTS / "final_comparison.md"),
    ], cwd=REPO)

    log(f"Done. See {REPORTS / 'final_comparison.md'}")


if __name__ == "__main__":
    main()
