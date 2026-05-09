"""Database configuration honors deployment environment."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_database_url_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib
    import app.core.database as database
    try:
        reloaded = importlib.reload(database)
        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            reloaded._get_database_url()
    finally:
        monkeypatch.setenv("DATABASE_URL", "postgresql://aihub:aihub_pass@localhost:5432/ai_hub")
        importlib.reload(database)
