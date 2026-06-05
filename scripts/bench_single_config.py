#!/home/hung/ai-hub/venv/bin/python3
"""Benchmark a single 12B configuration.

Assumes llama-server(s) are already running on 8080/8083.
Runs 7 phases: warmup, latency baseline, 5/10/20/40 users, Vietnamese quality.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from bench_metrics import compute_phase_metrics, aggregate_phases
from quality_scoring import PROMPT_BANK, score_response, detect_hallucination

BASE_URL = "http://127.0.0.1:8080/v1"
import httpx


def get_model_name(config: str) -> str:
    return {
        "Q4-combo": "local-gemma4-12b-q4-text",
        "Q6-combo": "local-gemma4-12b-q6-text",
        "Q8-standalone": "local-gemma4-12b-q8-mmproj",
        "Q8-textonly": "local-gemma4-12b-q8-text",
    }[config]


async def single_request(client: httpx.AsyncClient, model: str, prompt: str,
                        max_tokens: int = 200) -> dict:
    """Send one request, return timing dict + actual response text."""
    t0 = time.perf_counter()
    ttft = None
    response_text = ""
    status = "ok"
    try:
        async with client.stream("POST", f"{BASE_URL}/chat/completions",
                                  json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                        "max_tokens": max_tokens, "temperature": 0.2, "stream": True},
                                  timeout=60.0) as r:
            r.raise_for_status()
            async for chunk in r.aiter_text():
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
                # Parse SSE: each chunk may contain "data: {json}\n\n" lines
                for line in chunk.split("\n"):
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload)
                        delta = data["choices"][0]["delta"].get("content")
                        if delta:
                            response_text += delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
        e2e = (time.perf_counter() - t0) * 1000
        # crude token estimate: 1 token ≈ 4 chars
        completion_tokens = max(1, len(response_text) // 4)
        return {
            "ttft_ms": int(ttft or 0),
            "e2e_ms": int(e2e),
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": completion_tokens,
            "status": status,
            "response": response_text,
        }
    except Exception as e:
        return {
            "ttft_ms": 0, "e2e_ms": 0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "status": f"ERROR: {type(e).__name__}",
            "response": "",
        }


async def run_phase(phase_name: str, model: str, prompts: list[str],
                    concurrent: int, wall_time_s: float, client: httpx.AsyncClient) -> dict:
    """Run a phase: send all prompts concurrently, return aggregate metrics."""
    timings = []
    t0 = time.perf_counter()

    for i in range(0, len(prompts), concurrent):
        batch = prompts[i:i+concurrent]
        tasks = [single_request(client, model, p) for p in batch]
        batch_results = await asyncio.gather(*tasks)
        timings.extend(batch_results)
        if time.perf_counter() - t0 > wall_time_s:
            break

    actual_wall = time.perf_counter() - t0
    return {
        "phase": phase_name,
        "concurrent": concurrent,
        "prompt_count": len(timings),
        "wall_time_s": round(actual_wall, 1),
        **compute_phase_metrics(timings, actual_wall),
    }


PROMPTS_BY_PHASE = {
    "warmup": ["Xin chào"] * 5,
    "latency baseline": [f"Câu hỏi {i}: Giải thích ngắn về IoT trong công nghiệp" for i in range(10)],
    "concurrency_5":  [f"User {i}: Mô tả ngắn về cảm biến công nghiệp" for i in range(25)],
    "concurrency_10": [f"User {i}: Dịch 'industrial monitoring' sang tiếng Việt" for i in range(50)],
    "concurrency_20": [f"User {i}: Hãy giải thích về predictive maintenance" for i in range(60)],
    "concurrency_40": [f"User {i}: Tại sao cần IHI monitoring?" for i in range(60)],
}


async def run_quality_phase(model: str, client: httpx.AsyncClient) -> dict:
    """Run Vietnamese quality rubric on 10 sampled prompts. Uses actual response text."""
    samples = []
    for prompt_data in PROMPT_BANK[:10]:
        prompt = prompt_data["prompt"]
        result = await single_request(client, model, prompt, max_tokens=300)
        if result["status"] == "ok" and result["response"]:
            score = score_response(prompt, result["response"])
            samples.append({"prompt_id": prompt_data["id"], "category": prompt_data["category"], "score": score["total"]})
    if not samples:
        return {"samples": 0, "quality": 0}
    return {
        "samples": len(samples),
        "quality": round(sum(s["score"] for s in samples) / len(samples), 2),
    }


async def main(config: str, max_load: bool, output: str) -> int:
    print(f"[bench_single] Starting {config} (max_load={max_load})", flush=True)
    model = get_model_name(config)
    results = {"config": config, "model": model, "stages": {}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Health check
        try:
            r = await client.get(f"{BASE_URL.replace('/v1', '')}/models", timeout=5.0)
            r.raise_for_status()
        except Exception as e:
            print(f"ERROR: cannot reach {BASE_URL}: {e}")
            return 2

        # Phases
        for phase_name, prompts in PROMPTS_BY_PHASE.items():
            concurrent_map = {"warmup": 1, "latency baseline": 1, "concurrency_5": 5,
                              "concurrency_10": 10, "concurrency_20": 20, "concurrency_40": 40}
            if max_load and phase_name == "concurrency_20":
                # In max-load mode, extend concurrency 20 to 60 prompts
                prompts = prompts * 2
                wall = 600  # 10 min
            elif max_load:
                continue  # skip other phases in max-load mode
            else:
                wall = {"warmup": 30, "latency baseline": 60, "concurrency_5": 60,
                        "concurrency_10": 90, "concurrency_20": 120, "concurrency_40": 120}[phase_name]

            print(f"  Phase: {phase_name} (concurrent={concurrent_map[phase_name]}, wall={wall}s)", flush=True)
            result = await run_phase(phase_name, model, prompts, concurrent_map[phase_name], wall, client)
            results["stages"][phase_name] = result
            print(f"    tok/s: {result.get('tok_s_aggregate', 0)}, p95: {result.get('ttft_p95_ms', 0)}ms", flush=True)

        # Quality
        if not max_load:
            print("  Phase: quality", flush=True)
            q = await run_quality_phase(model, client)
            results["quality"] = q.get("quality", 0)
            print(f"    quality: {q.get('quality', 0)}/10", flush=True)

    # Aggregate
    agg = aggregate_phases(results["stages"])
    results["aggregate"] = agg
    # Placeholder composite_score; final report computes it across all configs
    results["composite_score"] = 0.0

    # Write
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[bench_single] Done. Results: {output}", flush=True)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, choices=["Q4-combo", "Q6-combo", "Q8-standalone", "Q8-textonly"])
    p.add_argument("--max-load", action="store_true")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.config, args.max_load, args.output)))
