#!/usr/bin/env python3
"""Seed RAG knowledge cards for testbed tenants from scenarios/knowledge.json.

Usage:  ./venv/bin/python scripts/testbed/seed_knowledge.py [--api http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_FILE = ROOT / "scripts" / "testbed" / "scenarios" / "knowledge.json"
ENV_FILE = ROOT / ".env"

# Tenant -> project mapping (matches scenario JSON files)
PROJECT_BY_TENANT = {
    "fanpage": "fanpage",
    "vehix": "vehix",
    "iot": "iot",
    "sales_bot": "sales_bot",
}


def _load_api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("API_KEY not found in .env")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    api_key = _load_api_key()
    payload = json.loads(KNOWLEDGE_FILE.read_text())
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    created = 0
    failed = 0

    with httpx.Client(timeout=30.0) as client:
        for tenant_key, cards in payload.items():
            project_id = PROJECT_BY_TENANT.get(tenant_key, tenant_key)
            tenant_id = "sales" if tenant_key == "sales_bot" else tenant_key
            for card in cards:
                body = {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "knowledge_domain": card.get("domain", "general"),
                    "title": card["title"],
                    "content": card["content"],
                    "trust_level": card.get("trust_level", 3),
                    "tags": card.get("tags", []),
                }
                resp = client.post(f"{args.api}/v1/knowledge/cards", json=body, headers=headers)
                if resp.status_code == 200:
                    created += 1
                else:
                    failed += 1
                    print(f"[seed] FAIL {tenant_id}/{card['title'][:40]}: {resp.status_code} {resp.text[:120]}")

    print(f"[seed] created={created} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
