"""Legacy entry shim — uvicorn main:app still works after the refactor."""

from app.main import app

__all__ = ["app"]
