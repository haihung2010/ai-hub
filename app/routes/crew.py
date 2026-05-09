"""POST /v1/crew/research - run the Researcher + Analyst crew for a query."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/crew", tags=["crew"])


class CrewResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    tenant_id: str | None = None


class CrewResearchResponse(BaseModel):
    query: str
    result: str


@router.post("/research", response_model=CrewResearchResponse)
async def crew_research(body: CrewResearchRequest, request: Request) -> CrewResearchResponse:
    """Run the CrewAI Researcher + Analyst pipeline and return the result."""
    api_key_tenant = getattr(request.state, "api_key_tenant_id", None)
    if api_key_tenant is not None and body.tenant_id is not None and api_key_tenant != body.tenant_id:
        raise HTTPException(status_code=403, detail="tenant_id mismatch")
    crew_service = getattr(request.app.state, "crew_service", None)
    if crew_service is None:
        raise HTTPException(
            status_code=503,
            detail="crew agents not enabled (set ENABLE_CREW_AGENTS=true)",
        )

    logger.info("crew research requested: %r", body.query)
    result = await crew_service.research(body.query)
    if not result:
        raise HTTPException(status_code=502, detail="crew returned empty result")

    return CrewResearchResponse(query=body.query, result=result)
