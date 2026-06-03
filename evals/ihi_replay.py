"""IHI replay — feed 7 days of real sensor data into ai-hub's IHI pipeline.

Reads /home/hung/ihi_test/sensors.db (SQLite, 4.6M measurements, 3 devices
over 7 days) and replays it through POST /v1/ihi/analyze in simulated
real-time. Each IHI call is one 1-minute bucket aggregated across devices:

  - Sensor-001: temperature → IHI t
                 max(velocity_x, velocity_y, velocity_z) → IHI v
  - Meter-001 : I1 (phase 1 current) → IHI c
  - PLC-001   : not used (different I/O, not IHI-mappable)

The IHI analyze endpoint then runs the rule-based analyzer + RAG
fallback against the 3-sensor aggregate. The script records each
alert level + per-call latency and prints a summary at the end.

Usage:
  ./venv/bin/python evals/ihi_replay.py \\
      --api-key <key> \\
      --hours 24          # replay only first 24h of data
      --speed 60          # 60x real-time (1 simulated minute = 1 wall second)
      --db /home/hung/ihi_test/sensors.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Default IHI mapping — each device has ONE role per metric
IHI_METRICS = {
    "Sensor-001": {
        "t": "temperature",                    # °C
        "v": ["velocity_x", "velocity_y", "velocity_z"],  # mm/s — take max
    },
    "Meter-001": {
        "c": "I1",                             # A (phase 1 current)
    },
}


@dataclass
class BucketReading:
    """Aggregated 1-minute bucket for one device."""
    device_id: str
    minute: str  # ISO8601 floor to minute
    t: float | None = None
    v: float | None = None
    c: float | None = None


@dataclass
class CallResult:
    minute: str
    alert: str
    devices: list[str]
    case_id: str | None
    latency_ms: float
    error: str = ""


# ── DB helpers ─────────────────────────────────────────────────────────


def _open_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _build_buckets(db_path: str, hours: int | None) -> list[BucketReading]:
    """Read measurements and aggregate to 1-minute buckets per device.

    Returns a list sorted by (minute, device_id) so we can replay in order.
    """
    con = _open_db(db_path)
    cur = con.cursor()
    where_clauses: list[str] = []
    params: list[object] = []
    if hours is not None:
        # Take only the first N hours of the dataset
        cur.execute("SELECT MIN(time) FROM measurements")
        first = cur.fetchone()[0]
        if first:
            # ISO8601 — use Python to add hours
            t0 = datetime.fromisoformat(first.replace("Z", "+00:00"))
            t1 = t0.timestamp() + hours * 3600
            where_clauses.append("time < ?")
            params.append(
                datetime.fromtimestamp(t1, tz=timezone.utc).isoformat()
                .replace("+00:00", "Z")
            )

    sql = """
        SELECT device, measurement, time, value
        FROM measurements
        WHERE measurement IN (
            'temperature', 'velocity_x', 'velocity_y', 'velocity_z', 'I1'
        )
    """
    if where_clauses:
        sql += " AND " + " AND ".join(where_clauses)
    sql += " ORDER BY time ASC"
    cur.execute(sql, tuple(params))

    # Aggregate: { (minute, device): {metric: [values] } }
    acc: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in cur:
        # Floor to minute
        t = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
        minute = t.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        key = (minute, row["device"])
        acc[key][row["measurement"]].append(float(row["value"]))
    con.close()

    # Build list of BucketReading
    out: list[BucketReading] = []
    for (minute, device), metrics in acc.items():
        b = BucketReading(device_id=device, minute=minute)
        t = statistics.median(metrics["temperature"]) if metrics.get("temperature") else None
        v_vals: list[float] = []
        for k in ("velocity_x", "velocity_y", "velocity_z"):
            v_vals.extend(metrics.get(k, []))
        v = max(v_vals) if v_vals else None
        c = statistics.median(metrics["I1"]) if metrics.get("I1") else None
        b.t, b.v, b.c = t, v, c
        out.append(b)
    out.sort(key=lambda b: (b.minute, b.device_id))
    return out


def _group_buckets_by_minute(buckets: list[BucketReading]) -> dict[str, list[BucketReading]]:
    grouped: dict[str, list[BucketReading]] = defaultdict(list)
    for b in buckets:
        grouped[b.minute].append(b)
    return grouped


# ── HTTP client (sync, stdlib only) ────────────────────────────────────


def _post_analyze(
    url: str, api_key: str, readings: list[BucketReading]
) -> tuple[CallResult | None, float, str]:
    """Call /v1/ihi/analyze. Returns (parsed_result, latency_ms, error)."""
    body = {
        "ts": readings[0].minute,
        "data": [
            {
                "id": r.device_id,
                "t": r.t if r.t is not None else 0.0,
                "v": r.v if r.v is not None else 0.0,
                "c": r.c if r.c is not None else 0.0,
            }
            for r in readings
            # Include any device that has at least one real measurement;
            # missing metrics become 0 (rule engine treats 0 as "no anomaly").
            if r.t is not None or r.v is not None or r.c is not None
        ],
    }
    if not body["data"]:
        return None, 0.0, "no devices in bucket"

    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{url}/v1/ihi/analyze",
        data=payload,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            latency = (time.monotonic() - started) * 1000
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        latency = (time.monotonic() - started) * 1000
        return None, latency, f"http {exc.code}: {exc.read().decode()[:200]}"
    except urllib.error.URLError as exc:
        latency = (time.monotonic() - started) * 1000
        return None, latency, f"url error: {exc}"

    return (
        CallResult(
            minute=readings[0].minute,
            alert=data.get("alert", "?"),
            devices=data.get("devices", []),
            case_id=data.get("case_id"),
            latency_ms=data.get("latency_ms", latency),
        ),
        latency,
        "",
    )


# ── Replay driver ──────────────────────────────────────────────────────


def run_replay(
    db_path: str,
    base_url: str,
    api_key: str,
    hours: int | None,
    speed: float,
    progress_every: int = 100,
) -> list[CallResult]:
    print(f"▸ Loading buckets from {db_path} (hours={hours})...")
    buckets = _build_buckets(db_path, hours)
    if not buckets:
        print("  ⚠ no buckets — check DB and hours filter")
        return []
    minutes = _group_buckets_by_minute(buckets)
    total = len(minutes)
    print(f"  {total} unique 1-minute buckets across {len({b.device_id for b in buckets})} devices")
    first_min = min(minutes.keys())
    last_min = max(minutes.keys())
    print(f"  Time range: {first_min} → {last_min}")
    print()

    if speed <= 0:
        raise ValueError("speed must be > 0")

    results: list[CallResult] = []
    alert_counts: dict[str, int] = defaultdict(int)
    last_min_dt: datetime | None = None
    wall_started = time.monotonic()
    sim_started = datetime.fromisoformat(first_min.replace("Z", "+00:00"))

    for i, minute_iso in enumerate(sorted(minutes.keys()), 1):
        sim_dt = datetime.fromisoformat(minute_iso.replace("Z", "+00:00"))
        if last_min_dt is not None:
            # Wait the real-time equivalent, divided by speed
            sim_delta = (sim_dt - last_min_dt).total_seconds()
            wall_delta = sim_delta / speed
            if wall_delta > 0:
                time.sleep(wall_delta)
        last_min_dt = sim_dt

        result, latency, err = _post_analyze(base_url, api_key, minutes[minute_iso])
        if result is None:
            result = CallResult(
                minute=minute_iso,
                alert="ERROR",
                devices=[],
                case_id=None,
                latency_ms=latency,
                error=err,
            )
        results.append(result)
        alert_counts[result.alert] += 1

        if i % progress_every == 0 or i == total:
            elapsed = time.monotonic() - wall_started
            sim_elapsed = (sim_dt - sim_started).total_seconds() / 60
            print(
                f"  [{i:5d}/{total}] {minute_iso}  "
                f"alert={result.alert:<6}  devices={result.devices}  "
                f"latency={result.latency_ms:6.0f}ms  "
                f"sim={sim_elapsed:6.1f}min wall={elapsed/60:5.1f}min"
            )

    print()
    print("=" * 80)
    print("  REPLAY SUMMARY")
    print("=" * 80)
    total_secs = time.monotonic() - wall_started
    print(f"  Total buckets:     {len(results)}")
    print(f"  Wall time:         {total_secs:.1f}s ({total_secs/60:.1f} min)")
    print(f"  Effective speed:   {len(results)/(total_secs/60):.1f} calls/min")
    print(f"  Alert distribution:")
    for level, count in sorted(alert_counts.items(), key=lambda x: -x[1]):
        pct = count / len(results) * 100 if results else 0
        print(f"    {level:<10}  {count:5d}  ({pct:5.1f}%)")
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    if latencies:
        print(f"  Latency: median={statistics.median(latencies):.0f}ms  "
              f"min={min(latencies):.0f}ms  max={max(latencies):.0f}ms")
    errors = [r for r in results if r.error]
    if errors:
        print(f"  Errors: {len(errors)} (sample: {errors[0].error[:100]})")
    print()
    return results


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay IHI sensor data through ai-hub")
    parser.add_argument("--db", default="/home/hung/ihi_test/sensors.db", help="SQLite DB path")
    parser.add_argument("--base-url", default="http://localhost:8000", help="ai-hub base URL")
    parser.add_argument("--api-key", required=True, help="ai-hub API key")
    parser.add_argument("--hours", type=int, default=24, help="Replay only first N hours (default 24)")
    parser.add_argument("--speed", type=float, default=60.0,
                        help="Acceleration factor (60 = 1 sim minute = 1 wall second)")
    parser.add_argument("--out", help="Save detailed results to JSON file")
    parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N buckets")
    args = parser.parse_args()

    results = run_replay(
        db_path=args.db,
        base_url=args.base_url,
        api_key=args.api_key,
        hours=args.hours,
        speed=args.speed,
        progress_every=args.progress_every,
    )
    if args.out and results:
        with open(args.out, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"  Detailed results saved to {args.out}")


if __name__ == "__main__":
    main()
