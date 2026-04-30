#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORTS = ROOT / "reports"
PYTHON = ROOT / "venv/bin/python"
LOADTEST = ROOT / "scripts/repeated_topic_loadtest.py"
LLAMA_SERVER = Path(os.getenv("LLAMA_SERVER", "/home/hung/llama.cpp/build-cuda13/bin/llama-server"))
MODEL = Path(os.getenv("Q8_MODEL", "/home/hung/models/gemma-4-E4B-it-obliterated-Q8_0.gguf"))
HOST = "127.0.0.1"
LLAMA_PORT = 8080
APP_PORT = 8000
ALIAS = "local-gemma4-e4b-q8"
PID_FILE = Path("/tmp/aihub-llama-server.pid")
LLAMA_LOG = Path("/tmp/aihub-autotune-llama.log")
APP_LOG = Path("/tmp/aihub-autotune-app.log")
SUMMARY_PATH = REPORTS / "q8_autotune_summary.json"


@dataclass(frozen=True)
class Case:
    name: str
    parallel: int
    ctx_size: int
    gpu_concurrency: int
    test_concurrency: int
    users: int = 10
    questions: int = 10
    timeout: int = 180


BASE_CASES = [
    Case("p6_ctx4k_gpu6_c6", 6, 24576, 6, 6),
    Case("p8_ctx8k_gpu8_c8", 8, 65536, 8, 8),
    Case("p8_ctx4k_gpu8_c8", 8, 32768, 8, 8),
    Case("p10_ctx4k_gpu10_c8", 10, 40960, 10, 8),
    Case("p10_ctx6k_gpu10_c8", 10, 61440, 10, 8),
    Case("p12_ctx4k_gpu12_c8", 12, 49152, 12, 8),
    Case("p12_ctx4k_gpu12_c10", 12, 49152, 12, 10),
    Case("p8_ctx8k_gpu8_c10", 8, 65536, 8, 10),
]


def run(cmd: list[str], *, timeout: int = 300, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def update_env(gpu_concurrency: int) -> None:
    text = ENV_PATH.read_text()
    text = re.sub(r"^GPU_CONCURRENCY=.*$", f"GPU_CONCURRENCY={gpu_concurrency}", text, flags=re.MULTILINE)
    text = re.sub(r"^HYBRID_LOCAL_QUEUE_TIMEOUT_SECONDS=.*$", "HYBRID_LOCAL_QUEUE_TIMEOUT_SECONDS=8", text, flags=re.MULTILINE)
    text = re.sub(
        r"^# AI Hub production default: Lite Q8, Thinking Qwen 27B, app concurrency .*$",
        f"# AI Hub production default: Lite Q8, Thinking Qwen 27B, app concurrency {gpu_concurrency}",
        text,
        flags=re.MULTILINE,
    )
    ENV_PATH.write_text(text)


def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_by_port(port: int) -> None:
    result = run(["bash", "-lc", f"ss -ltnp | grep ':{port}\\b' || true"], timeout=30)
    for pid_text in re.findall(r"pid=(\d+)", result.stdout):
        kill_pid(int(pid_text))


def start_llama(case: Case) -> None:
    stop_by_port(LLAMA_PORT)
    if PID_FILE.exists():
        try:
            kill_pid(int(PID_FILE.read_text().strip()))
        except ValueError:
            pass
        PID_FILE.unlink(missing_ok=True)

    log = LLAMA_LOG.open("w")
    proc = subprocess.Popen(
        [
            str(LLAMA_SERVER),
            "-m",
            str(MODEL),
            "--host",
            HOST,
            "--port",
            str(LLAMA_PORT),
            "--ctx-size",
            str(case.ctx_size),
            "--parallel",
            str(case.parallel),
            "--n-gpu-layers",
            "999",
            "--alias",
            ALIAS,
            "--reasoning",
            "off",
        ],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    PID_FILE.write_text(str(proc.pid))
    deadline = time.time() + 240
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited for {case.name}: {LLAMA_LOG.read_text(errors='replace')[-4000:]}")
        check = run(["curl", "-fsS", f"http://localhost:{LLAMA_PORT}/v1/models"], timeout=10)
        if check.returncode == 0:
            return
        time.sleep(0.5)
    raise TimeoutError(f"llama-server readiness timeout for {case.name}")


def start_app(case: Case) -> subprocess.Popen[str]:
    stop_by_port(APP_PORT)
    update_env(case.gpu_concurrency)
    log = APP_LOG.open("w")
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "localhost", "--port", str(APP_PORT)],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"AI Hub exited for {case.name}: {APP_LOG.read_text(errors='replace')[-4000:]}")
        env_values = read_env()
        api_key = env_values.get("API_KEY", "")
        smoke = run(
            [
                "curl",
                "-fsS",
                "-X",
                "POST",
                f"http://localhost:{APP_PORT}/v1/chat",
                "-H",
                "Content-Type: application/json",
                "-H",
                f"X-API-KEY: {api_key}",
                "-d",
                '{"project_id":"test","tenant_id":"autotune","user_name":"smoke","user_message":"ok","model_mode":"lite","allow_external":true}',
            ],
            timeout=30,
        )
        if smoke.returncode == 0:
            return proc
        time.sleep(0.5)
    raise TimeoutError(f"AI Hub readiness timeout for {case.name}")


def run_loadtest(case: Case) -> dict[str, Any]:
    env_values = read_env()
    env = os.environ.copy()
    env.update(env_values)
    env.update(
        {
            "AIHUB_API_KEY": env_values.get("API_KEY", ""),
            "AIHUB_LOADTEST_URL": f"http://localhost:{APP_PORT}",
            "AIHUB_LOADTEST_USERS": str(case.users),
            "AIHUB_LOADTEST_QUESTIONS": str(case.questions),
            "AIHUB_LOADTEST_MAX_CONCURRENCY": str(case.test_concurrency),
            "AIHUB_LOADTEST_TIMEOUT": str(case.timeout),
            "AIHUB_LOADTEST_ALLOW_EXTERNAL": "true",
            "AIHUB_LOADTEST_MODEL_MODE": "lite",
            "AIHUB_LOADTEST_REPORT": f"autotune_{case.name}",
        }
    )
    started = time.time()
    proc = run([str(PYTHON), str(LOADTEST)], timeout=900, env=env)
    report_path = REPORTS / f"autotune_{case.name}"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        summary = report["summary"]
    else:
        summary = {"total_ok": 0, "total_errors": case.users * case.questions, "run_failed": True}
    return {
        "case": asdict(case),
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
        "summary": summary,
    }


def score(result: dict[str, Any]) -> tuple[int, float, float, float]:
    summary = result["summary"]
    errors = int(summary.get("total_errors", 999999))
    p95 = float(summary.get("latency_p95_s", 999999))
    wall = float(summary.get("wall_time_s", 999999))
    p99 = float(summary.get("latency_p99_s", 999999))
    return errors, p95, wall, p99


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    original_env = ENV_PATH.read_text()

    try:
        for case in BASE_CASES:
            print(f"\n=== CASE {case.name} ===", flush=True)
            try:
                start_llama(case)
                app = start_app(case)
                result = run_loadtest(case)
                results.append(result)
                print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
                kill_pid(app.pid)
            except Exception as exc:
                result = {"case": asdict(case), "error": repr(exc), "summary": {"total_errors": case.users * case.questions}}
                results.append(result)
                print(f"FAILED {case.name}: {exc!r}", flush=True)
            SUMMARY_PATH.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2))

        valid = [item for item in results if "summary" in item]
        best = sorted(valid, key=score)[0] if valid else None
        SUMMARY_PATH.write_text(json.dumps({"best": best, "results": results}, ensure_ascii=False, indent=2))
        print("\n=== BEST ===", flush=True)
        print(json.dumps(best, ensure_ascii=False, indent=2), flush=True)
    finally:
        if results:
            best = sorted([item for item in results if "summary" in item], key=score)[0]
            best_case = Case(**best["case"])
            start_llama(best_case)
            update_env(best_case.gpu_concurrency)
            start_app(best_case)
        else:
            ENV_PATH.write_text(original_env)


if __name__ == "__main__":
    main()
