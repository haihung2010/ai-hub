"""CrewAI Analyst agent - analyzes data from the DB and research output."""

from __future__ import annotations

from crewai import Agent, LLM

from app.agents.tools import DBConnectorTool


def make_analyst(model: str, base_url: str, db_tool: DBConnectorTool) -> Agent:
    """Return an Analyst agent backed by the given Ollama model."""
    llm = LLM(model=f"ollama/{model}", base_url=base_url)
    return Agent(
        role="Data Analyst",
        goal=(
            "Analyze research findings and historical chat data to produce clear, "
            "actionable insights."
        ),
        backstory=(
            "You are an expert data analyst skilled at querying databases, recognizing "
            "patterns, and synthesizing information from multiple sources into concise, "
            "well-reasoned conclusions."
        ),
        tools=[db_tool],
        llm=llm,
        verbose=False,
        allow_delegation=False,
        max_iter=3,
    )
