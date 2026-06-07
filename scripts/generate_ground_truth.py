#!/usr/bin/env python3
"""Generate ground truth labels for IHI test suite.

Uses heuristic rules on historical data to label clear cases,
flags disagreements with LLM verdicts for operator review.
"""
import json
import re
import sqlite3
from pathlib import Path

ALERT_DB = Path("/home/hung/ihi_test/alert.db")
OUTPUT_DIR = Path(__file__).parent.parent / "tests" / "ground_truth"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def heuristic_label(readings: dict):
    """Return (alert, symptom) for clear cases, None for ambiguous."""
    if readings.get("battery_pct") is not None and readings["battery_pct"] < 5:
        return "DANGER", "battery_critical"
    if readings.get("v_imbalance_pct") is not None and readings["v_imbalance_pct"] > 5:
        return "DANGER", "voltage_imbalance"
    if readings.get("AI1_voltage") is not None and readings["AI1_voltage"] > 100:
        return "DANGER", "sensor_broken"
    if readings.get("temperature") is not None and readings["temperature"] > 90:
        return "DANGER", "overheat"
    if readings.get("velocity_rms") is not None and readings["velocity_rms"] > 4.5:
        return "DANGER", "excessive_vibration"
    # All safe
    if all(v is None for v in readings.values()):
        return None
    # Conservative: if all readings in safe zone
    safe = True
    if readings.get("temperature") and readings["temperature"] > 80: safe = False
    if readings.get("velocity_rms") and readings["velocity_rms"] > 2.8: safe = False
    if readings.get("v_imbalance_pct") and readings["v_imbalance_pct"] > 2: safe = False
    if readings.get("battery_pct") and readings["battery_pct"] < 20: safe = False
    if safe:
        return "NORMAL", "baseline"
    return None  # ambiguous, skip


def main():
    if not ALERT_DB.exists():
        print(f"alert.db not found at {ALERT_DB}")
        return
    con = sqlite3.connect(str(ALERT_DB))
    con.row_factory = sqlite3.Row
    scrapes = con.execute("SELECT id FROM scrapes ORDER BY id").fetchall()
    labeled = []
    review_queue = []
    for s in scrapes:
        sid = s["id"]
        verdicts = con.execute(
            "SELECT id, phase, verdict_text FROM verdicts WHERE scrape_id = ?", (sid,)
        ).fetchall()
        for v in verdicts:
            # Get readings for this verdict's snapshot
            snap = con.execute(
                "SELECT id FROM snapshots WHERE scrape_id = ? AND phase = ?",
                (sid, v["phase"]),
            ).fetchone()
            if not snap:
                continue
            readings_rows = con.execute(
                "SELECT device, measurement, value FROM snapshot_readings WHERE snapshot_id = ?",
                (snap["id"],),
            ).fetchall()
            readings = {}
            for r in readings_rows:
                readings[r["measurement"]] = r["value"]
            # Heuristic label
            h = heuristic_label(readings)
            if h is None:
                continue
            heuristic_alert, symptom = h
            # Parse LLM verdict
            m = re.search(r"\*\*Verdict:\*\*\s*(NORMAL|WARNING|DANGER)", v["verdict_text"] or "")
            llm_alert = m.group(1) if m else "?"
            case = {
                "id": f"gt-{sid:03d}-p{v['phase']}",
                "scrape_id": sid,
                "phase": v["phase"],
                "readings": readings,
                "expected_alert": heuristic_alert,
                "expected_symptom": symptom,
                "llm_alert": llm_alert,
            }
            if llm_alert != "?" and llm_alert != heuristic_alert:
                review_queue.append(case)
            else:
                labeled.append(case)
    con.close()
    # Write outputs
    out_v1 = OUTPUT_DIR / "ground_truth_v1.jsonl"
    with out_v1.open("w") as f:
        for c in labeled:
            f.write(json.dumps(c) + "\n")
    out_review = OUTPUT_DIR / "ground_truth_review.jsonl"
    with out_review.open("w") as f:
        for c in review_queue:
            f.write(json.dumps(c) + "\n")
    print(f"Labeled: {len(labeled)}, Needs review: {len(review_queue)}")
    print(f"Written to: {out_v1}")


if __name__ == "__main__":
    main()
