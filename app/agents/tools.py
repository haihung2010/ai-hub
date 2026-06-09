"""CrewAI-native tool definitions (BaseTool) for Researcher and Analyst agents."""

from __future__ import annotations

import logging

import psycopg
from crewai.tools import BaseTool
from ddgs import DDGS
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def _get_pool(db_url: str) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(db_url, min_size=1, max_size=4, kwargs={"row_factory": dict_row})
    return _pool


def reset_pool() -> None:
    """Close and clear the module-level pool (used by tests and shutdown)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for current, real-time information. Input: a search query string."

    def _run(self, query: str) -> str:
        try:
            with DDGS(timeout=10) as d:
                results = list(d.text(query, max_results=5))
            return str([{"title": r.get("title", ""), "snippet": r.get("body", "")} for r in results])
        except Exception as e:
            logger.warning("WebSearchTool failed: %s", e)
            return "No results found."


class DBConnectorTool(BaseTool):
    name: str = "query_chat_history"
    description: str = "Run a read-only SQL SELECT query against the chat history PostgreSQL database. Input: a valid SQL SELECT statement."
    db_url: str = ""

    def _run(self, sql: str) -> str:
        if not sql.strip().upper().startswith("SELECT"):
            return "Only SELECT queries are allowed."
        try:
            with _get_pool(self.db_url).connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = '5s'")
                    cur.execute(sql)
                    rows = cur.fetchmany(20)
            return str([dict(r) for r in rows])
        except Exception as e:
            logger.warning("DBConnectorTool failed: %s", e)
            return f"Query error: {e}"


def make_search_tool() -> WebSearchTool:
    """Web search tool powered by DuckDuckGo."""
    return WebSearchTool()


def make_db_connector(db_url: str) -> DBConnectorTool:
    """Read-only PostgreSQL connector for querying chat history tables."""
    return DBConnectorTool(db_url=db_url)
