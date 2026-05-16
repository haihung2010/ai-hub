#!/usr/bin/env python3
"""Ingest vehix legal knowledge cards (Nghị định 168/2024 + thuế/hợp đồng/bảo hiểm/kinh doanh vận tải).

Usage: ./venv/bin/python scripts/testbed/ingest_vehix_legal.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "scripts" / "testbed" / "scenarios"
LEGAL_FILES = ["vehix_legal.json", "vehix_legal_extra.json"]
ENV_FILE = ROOT / ".env"


def _load_api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY not found in .env")


def main() -> int:
    api = "http://localhost:8000"
    api_key = _load_api_key()
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    total_created = 0
    total_failed = 0
    with httpx.Client(timeout=30.0) as client:
        for filename in LEGAL_FILES:
            path = SCENARIO_DIR / filename
            if not path.exists():
                print(f"[skip] {filename} not found")
                continue
            payload = json.loads(path.read_text())
            tenant_id = payload["tenant_id"]
            project_id = payload["project_id"]
            trust = payload.get("trust_level", 3)
            source = payload.get("source", "manual")
            cards = payload["cards"]
            print(f"\n[file] {filename} — {len(cards)} cards (source: {source})")

            for card in cards:
                body = {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "knowledge_domain": card.get("domain", "legal_general"),
                    "title": card["title"],
                    "summary": source,
                    "content": card["content"],
                    "trust_level": trust,
                    "tags": card.get("tags", []),
                }
                resp = client.post(f"{api}/v1/knowledge/cards", json=body, headers=headers)
                if resp.status_code == 200:
                    total_created += 1
                    print(f"  [ok] {card['title'][:60]}")
                else:
                    total_failed += 1
                    print(f"  [FAIL] {card['title'][:50]}: {resp.status_code} {resp.text[:120]}")

    print(f"\n[ingest] vehix legal: created={total_created} failed={total_failed}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
