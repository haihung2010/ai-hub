"""Async wrapper that runs a CrewAI research crew in a thread-pool executor."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from crewai import Crew, Task

from app.agents.analyst import make_analyst
from app.agents.researcher import make_researcher
from app.agents.tools import make_db_connector, make_search_tool
from app.core.config import Settings

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crew")


class CrewService:
    """Orchestrates a Researcher + Analyst crew against Ollama."""

    def __init__(self, settings: Settings, db_path: str) -> None:
        self._settings = settings
        self._search_tool = make_search_tool()
        self._db_tool = make_db_connector(db_path)

    # ------------------------------------------------------------------
    # Sync execution (runs inside thread pool)
    # ------------------------------------------------------------------

    def _run(self, query: str) -> str:
        model = self._settings.crew_model
        base_url = self._settings.ollama_base_url

        researcher = make_researcher(model, base_url, self._search_tool)
        analyst = make_analyst(model, base_url, self._db_tool)

        research_task = Task(
            description=f"Research the following topic and gather key facts: {query}",
            expected_output="A comprehensive summary of findings with key facts and source URLs.",
            agent=researcher,
        )
        analysis_task = Task(
            description=(
                f"Using the research output and the chat-history database, analyze and "
                f"provide insights about: {query}"
            ),
            expected_output=(
                "Analytical insights that combine the latest research findings with "
                "relevant historical context from the chat database."
            ),
            agent=analyst,
            context=[research_task],
        )

        crew = Crew(
            agents=[researcher, analyst],
            tasks=[research_task, analysis_task],
            verbose=False,
        )
        result = crew.kickoff()
        return str(result)

    # ------------------------------------------------------------------
    # Async public API
    # ------------------------------------------------------------------

    async def research(self, query: str) -> str:
        """Run the crew asynchronously; returns the analyst's final output."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(_executor, partial(self._run, query))
        except Exception:
            logger.exception("CrewAI research failed for query=%r", query)
            return ""
