"""CrewAI-native tool definitions (BaseTool) for Researcher and Analyst agents."""

from __future__ import annotations

import logging
import sqlite3

from crewai.tools import BaseTool
from ddgs import DDGS

logger = logging.getLogger(__name__)


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
    description: str = "Run a read-only SQL SELECT query against the chat history SQLite database. Input: a valid SQL SELECT statement."
    db_path: str = "ai_hub.db"

    def _run(self, sql: str) -> str:
        if not sql.strip().upper().startswith("SELECT"):
            return "Only SELECT queries are allowed."
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql).fetchmany(20)
            conn.close()
            return str([dict(r) for r in rows])
        except Exception as e:
            logger.warning("DBConnectorTool failed: %s", e)
            return f"Query error: {e}"


def make_search_tool() -> WebSearchTool:
    """Web search tool powered by DuckDuckGo."""
    return WebSearchTool()


def make_db_connector(db_path: str) -> DBConnectorTool:
    """Read-only SQLite connector for querying chat history tables."""
    return DBConnectorTool(db_path=db_path)
