#!/usr/bin/env python3
"""So sánh side-by-side phản hồi giữa 2 báo cáo run_chats.

Usage:
  ./venv/bin/python scripts/testbed/compare.py REPORT_A REPORT_B [--tenant fanpage]

Mỗi turn in ra: tenant/user/turn, câu hỏi, latency A vs B, đáp án A vs B.
Kèm chỉ số tổng theo tenant.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import textwrap
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def _index(report: dict) -> dict:
    return {
        (r["tenant"], r["user"], r["turn"]): r
        for r in report["results"]
        if r.get("ok")
    }


def _wrap(text: str, width: int = 80, indent: str = "    ") -> str:
    if not text:
        return f"{indent}<rỗng>"
    out = []
    for paragraph in text.strip().splitlines():
        if not paragraph.strip():
            continue
        out.extend(textwrap.wrap(paragraph, width=width, initial_indent=indent, subsequent_indent=indent))
    return "\n".join(out) if out else f"{indent}<rỗng>"


def _summary_line(label: str, items: list[dict]) -> str:
    latencies = [r["latency_ms"] for r in items]
    avg = statistics.mean(latencies) if latencies else 0
    p50 = statistics.median(latencies) if latencies else 0
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0
    return f"  {label:14s} n={len(items):3d} avg={avg:7.0f}ms p50={p50:7.0f}ms p95={p95:7.0f}ms"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_a", type=Path)
    parser.add_argument("report_b", type=Path)
    parser.add_argument("--tenant", default=None, help="Lọc theo 1 tenant duy nhất")
    parser.add_argument("--user", default=None, help="Lọc theo 1 user duy nhất")
    parser.add_argument("--width", type=int, default=88)
    args = parser.parse_args()

    a = _load(args.report_a)
    b = _load(args.report_b)
    a_idx = _index(a)
    b_idx = _index(b)

    keys = sorted(set(a_idx) | set(b_idx))
    if args.tenant:
        keys = [k for k in keys if k[0] == args.tenant]
    if args.user:
        keys = [k for k in keys if k[1] == args.user]

    print("=" * args.width)
    print(f"A: {args.report_a.name}  total={a['duration_seconds']}s")
    print(f"B: {args.report_b.name}  total={b['duration_seconds']}s")
    print("=" * args.width)

    by_tenant: dict[str, dict[str, list[dict]]] = {}
    current = None
    for key in keys:
        tenant, user, turn = key
        if tenant != current:
            if current is not None:
                print()
            print(f"\n──── TENANT: {tenant} ────")
            current = tenant
        ra = a_idx.get(key)
        rb = b_idx.get(key)
        sample = ra or rb
        question = sample.get("question", "<no question>") if sample else ""
        la = ra["latency_ms"] if ra else None
        lb = rb["latency_ms"] if rb else None
        ma = ra.get("model", "?") if ra else "—"
        mb = rb.get("model", "?") if rb else "—"

        print(f"\n[{tenant}] {user} • turn {turn}")
        print(f"  Q: {question}")
        print(f"  A ({ma}, {la}ms):")
        print(_wrap(ra.get("answer", "") if ra else "<missing>", width=args.width - 4))
        print(f"  B ({mb}, {lb}ms):")
        print(_wrap(rb.get("answer", "") if rb else "<missing>", width=args.width - 4))

        by_tenant.setdefault(tenant, {"a": [], "b": []})
        if ra:
            by_tenant[tenant]["a"].append(ra)
        if rb:
            by_tenant[tenant]["b"].append(rb)

    print("\n" + "=" * args.width)
    print("Tổng kết latency theo tenant")
    print("=" * args.width)
    for tenant, groups in sorted(by_tenant.items()):
        print(f"\n{tenant}")
        print(_summary_line(f"A {args.report_a.stem}", groups["a"]))
        print(_summary_line(f"B {args.report_b.stem}", groups["b"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
