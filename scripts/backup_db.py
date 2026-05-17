#!/usr/bin/env python3
"""Backup the AI Hub PostgreSQL database using pg_dump.

Usage:
    python scripts/backup_db.py

Reads DATABASE_URL from environment. Writes a custom-format dump to
backups/ai_hub_YYYYMMDD_HHMMSS.dump and keeps the 7 most-recent files.

Suggested cron (daily at 02:00):
    0 2 * * * cd /path/to/ai-hub && ./venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1

Restore:
    pg_restore -d "$DATABASE_URL" --clean --if-exists backups/<file>.dump
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 7


def backup() -> Path:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    if shutil.which("pg_dump") is None:
        print("ERROR: pg_dump not found in PATH", file=sys.stderr)
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"ai_hub_{timestamp}.dump"

    cmd = ["pg_dump", "--format=custom", "--file", str(dest), db_url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: pg_dump failed: {result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)

    print(f"Backup created: {dest} ({dest.stat().st_size // 1024} KB)")
    return dest


def prune() -> None:
    backups = sorted(BACKUP_DIR.glob("ai_hub_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS:]:
        old.unlink()
        print(f"Deleted old backup: {old.name}")


if __name__ == "__main__":
    backup()
    prune()
