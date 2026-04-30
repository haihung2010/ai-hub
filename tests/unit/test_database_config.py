"""Database path configuration honors deployment environment."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.mark.unit
def test_database_path_honors_database_path_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_file = tmp_path / "custom" / "ai_hub.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))

    import app.core.database as database

    try:
        reloaded = importlib.reload(database)
        assert reloaded.DB_PATH == db_file
    finally:
        monkeypatch.delenv("DATABASE_PATH", raising=False)
        importlib.reload(database)
