#!/usr/bin/env python3
"""Backup the configured AI Hub SQLite database using VACUUM INTO.

Usage:
    python scripts/backup_db.py

The backup is written to backups/ai_hub_YYYYMMDD_HHMMSS.db.
Keeps the 7 most-recent backups; older ones are deleted.

Suggested cron (daily at 02:00):
    0 2 * * * cd /path/to/ai-hub && ./venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.core.database import DB_PATH

BACKUP_DIR = Path("backups")
MAX_BACKUPS = 7


def backup() -> Path:
    if not DB_PATH.is_file():
        print(f"ERROR: source database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"ai_hub_{timestamp}.db"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("VACUUM INTO ?", (str(dest),))

    print(f"Backup created: {dest} ({dest.stat().st_size // 1024} KB)")
    return dest


def prune() -> None:
    backups = sorted(BACKUP_DIR.glob("ai_hub_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS:]:
        old.unlink()
        print(f"Deleted old backup: {old.name}")


if __name__ == "__main__":
    backup()
    prune()
