"""CrewAI Researcher agent u2014 gathers information from the web."""

from __future__ import annotations

from crewai import Agent, LLM

from app.agents.tools import WebSearchTool


def make_researcher(model: str, base_url: str, search_tool: WebSearchTool) -> Agent:
    """Return a Researcher agent backed by the local llama.cpp model."""
    llm = LLM(model=f"openai/{model}", base_url=base_url)
    return Agent(
        role="Web Researcher",
        goal="Search the web and gather accurate, up-to-date information on any topic.",
        backstory=(
            "You are an expert researcher with years of experience finding reliable "
            "information online. You always verify sources and deliver comprehensive, "
            "well-structured findings."
        ),
        tools=[search_tool],
        llm=llm,
        verbose=False,
        allow_delegation=False,
        max_iter=3,
    )
