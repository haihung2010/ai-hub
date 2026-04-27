"""Manual smoke test against a running AI Hub instance.

Usage:
    python scripts/test_api.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys

import requests


def _pretty(label: str, data: object) -> None:
    print(f"\n== {label} ==")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run(base_url: str) -> int:
    base_url = base_url.rstrip("/")

    try:
        _pretty("GET /", requests.get(f"{base_url}/", timeout=5).json())
        _pretty("GET /health", requests.get(f"{base_url}/health", timeout=10).json())
    except requests.RequestException as exc:
        print(f"server unreachable: {exc}", file=sys.stderr)
        return 2

    for project_id, prompt in (
        ("iot", "AQI Ha Noi hom nay 180, toi nen lam gi?"),
        ("vehix", "Toi can thue xe 7 cho cuoi tuan nay, gia bao nhieu?"),
    ):
        try:
            resp = requests.post(
                f"{base_url}/v1/chat",
                json={"project_id": project_id, "user_message": prompt},
                timeout=120,
            )
        except requests.RequestException as exc:
            print(f"[{project_id}] request failed: {exc}", file=sys.stderr)
            return 3
        _pretty(f"POST /v1/chat ({project_id}) [{resp.status_code}]", resp.json())

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Hub smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    sys.exit(run(args.base_url))


if __name__ == "__main__":
    main()
