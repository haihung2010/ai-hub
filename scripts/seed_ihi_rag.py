#!/usr/bin/env python3
"""Seed initial RAG cases for IHI monitoring.

Usage:
    AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 ./venv/bin/python scripts/seed_ihi_rag.py

The env var is required to allow seeding when cases already exist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]

SEED_CASES = [
    {
        "case_id": "RAG-001",
        "severity": "CRITICAL",
        "symptom": "overheat",
        "pattern": {"t_min": 90, "t_max": 100, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65},
        "description": "Motor quá nhiệt (>90°C), cần kiểm tra cooling system",
        "resolution": "Check fan, clean heatsink, verify load",
    },
    {
        "case_id": "RAG-002",
        "severity": "CRITICAL",
        "symptom": "excessive_vibration",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 6.0, "v_max": 10.0, "c_min": 0, "c_max": 65},
        "description": "Rung quá mức (>6.0mm/s), có thể do bearing hỏng",
        "resolution": "Kiểm tra bearing, mount bolts, alignment",
    },
    {
        "case_id": "RAG-003",
        "severity": "CRITICAL",
        "symptom": "overload",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 0, "v_max": 4.5, "c_min": 75, "c_max": 100},
        "description": "Quá tải dòng điện (>75A)",
        "resolution": "Check motor windings, verify load, check power supply",
    },
    {
        "case_id": "RAG-004",
        "severity": "CRITICAL",
        "symptom": "overheat_vibration",
        "pattern": {"t_min": 85, "t_max": 100, "v_min": 5.0, "v_max": 8.0, "c_min": 60, "c_max": 80},
        "description": "Motor overheating kèm vibration cao - bearing wear sắp xảy ra",
        "resolution": "Kiểm tra bearing, verify lubrication",
    },
    {
        "case_id": "RAG-005",
        "severity": "WARNING",
        "symptom": "overheat_precursor",
        "pattern": {"t_min": 85, "t_max": 90, "v_min": 0, "v_max": 4.5, "c_min": 0, "c_max": 65},
        "description": "Nhiệt tiền nguy hiểm (85-90°C)",
        "resolution": "Monitor closely, plan maintenance",
    },
    {
        "case_id": "RAG-006",
        "severity": "WARNING",
        "symptom": "vibration_precursor",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 4.5, "v_max": 6.0, "c_min": 0, "c_max": 65},
        "description": "Rung tiền nguy hiểm (4.5-6.0mm/s)",
        "resolution": "Schedule inspection, check mounting",
    },
    {
        "case_id": "RAG-007",
        "severity": "WARNING",
        "symptom": "overload_precursor",
        "pattern": {"t_min": 0, "t_max": 85, "v_min": 0, "v_max": 4.5, "c_min": 65, "c_max": 75},
        "description": "Dòng cao tiền nguy (65-75A)",
        "resolution": "Monitor current, check load trend",
    },
    {
        "case_id": "RAG-008",
        "severity": "CRITICAL",
        "symptom": "multi_param",
        "pattern": {"t_min": 85, "t_max": 95, "v_min": 4.5, "v_max": 6.0, "c_min": 65, "c_max": 80},
        "description": "2+ thông số bất thường đồng thời",
        "resolution": "Full inspection required",
    },
    {
        "case_id": "RAG-009",
        "severity": "INFO",
        "symptom": "normal_high",
        "pattern": {"t_min": 80, "t_max": 85, "v_min": 3.0, "v_max": 4.5, "c_min": 55, "c_max": 65},
        "description": "Gần ngưỡng bình thường, cần theo dõi",
        "resolution": "Continue monitoring",
    },
    {
        "case_id": "RAG-010",
        "severity": "CRITICAL",
        "symptom": "sudden_spike",
        "pattern": {"t_min": 90, "t_max": 100, "v_min": 6.0, "v_max": 10.0, "c_min": 75, "c_max": 100},
        "description": "Đột biến đột ngột - cả 3 thông số đều vượt ngưỡng",
        "resolution": "Emergency shutdown and inspection",
    },
]


def _load_db_url() -> str:
    env_path = ROOT / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not found in .env")


async def main():
    db_url = _load_db_url()

    allow_truncate = os.environ.get("AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS") == "1"

    print("=== Seeding IHI RAG cases ===")
    print(f"Cases to seed: {len(SEED_CASES)}")

    conn = psycopg.connect(db_url, row_factory=dict_row)
    cur = conn.cursor()

    # Check existing count
    cur.execute("SELECT COUNT(*) as cnt FROM ihi_rag_cases")
    existing = cur.fetchone()["cnt"]
    print(f"Existing cases in DB: {existing}")

    if existing > 0 and not allow_truncate:
        print("WARNING: DB already has cases. Set AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 to replace.")
        print("Skipping seed.")
        cur.close()
        conn.close()
        return

    if existing > 0 and allow_truncate:
        print("Truncating existing ihi_rag_cases (ALLOW_DB_TRUNCATE_FOR_TESTS=1)")
        cur.execute("TRUNCATE TABLE ihi_rag_cases RESTART IDENTITY CASCADE")
        conn.commit()

    inserted = 0
    for case in SEED_CASES:
        cur.execute("""
            INSERT INTO ihi_rag_cases
                (device_id, severity, symptom, pattern, description, resolution, confirmed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            case["case_id"],
            case["severity"].lower(),
            case["symptom"],
            json.dumps(case["pattern"]),
            case["description"],
            case["resolution"],
            "system_seed",
        ))
        row = cur.fetchone()
        print(f"  ✓ {case['case_id']} (db_id={row['id']}) severity={case['severity']} symptom={case['symptom']}")
        inserted += 1

    conn.commit()

    # Verify
    cur.execute("SELECT COUNT(*) as cnt FROM ihi_rag_cases")
    total = cur.fetchone()["cnt"]

    cur.close()
    conn.close()

    print(f"\nInserted {inserted} cases. Total in DB: {total}")
    print("=== Done ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())