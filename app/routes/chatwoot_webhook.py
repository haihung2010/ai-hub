"""Chatwoot integration endpoints.

Two paths, two roles:

1. **POST /v1/integrations/chatwoot/respond** — Captain Custom Tool endpoint.
   Chatwoot Captain calls this synchronously and reads the JSON response.
   Used for AI Assistant auto-reply and Co-Pilot draft.

2. **POST /v1/integrations/chatwoot/agent_bot** — AgentBot webhook.
   Chatwoot AgentBot posts incoming customer message here. AI Hub processes
   it and posts reply back to conversation.message_url.

Auth:
- X-API-KEY (mandatory, AI Hub master key or per-tenant key)
- X-Chatwoot-Signature (optional HMAC-SHA256 of raw body using
  CHATWOOT_WEBHOOK_SECRET). If env var not set, signature is not verified
  (dev only). When set, missing or invalid signature → 401.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from app.models.chatwoot import (
    EscalateHumanRequest,
    EscalateHumanResponse,
    OrderStatusRequest,
    OrderStatusResponse,
    ProductLookupRequest,
    ProductLookupResponse,

    ChatwootAgentBotPayload,
    ChatwootAgentBotResponse,
    ChatwootCustomToolRequest,
    ChatwootCustomToolResponse,
)
from app.services import chatwoot_integration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/integrations/chatwoot", tags=["chatwoot"])


def _get_webhook_secret() -> str:
    return os.environ.get("CHATWOOT_WEBHOOK_SECRET", "")


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """Verify X-Chatwoot-Signature header against HMAC-SHA256(raw_body, secret).

    Security policy (P0 fix, 2026-06-09):
    - If CHATWOOT_WEBHOOK_SECRET is unset, REFUSE the request
      unless CHATWOOT_ALLOW_INSECURE=true (dev mode only).
    - In production, the secret MUST be set. Operators are expected
      to configure the secret alongside CHATWOOT_API_TOKEN.
    - This prevents an attacker who has learned the webhook URL from
      invoking AI tools (and running up the GPU bill) without the
      Chatwoot-side shared secret.
    """
    secret = _get_webhook_secret()
    if not secret:
        if os.environ.get("CHATWOOT_ALLOW_INSECURE", "").lower() == "true":
            return True  # dev mode: explicitly opted in
        logger.error(
            "Chatwoot HMAC secret not configured and CHATWOOT_ALLOW_INSECURE "
            "is not 'true'. Rejecting request — set CHATWOOT_WEBHOOK_SECRET "
            "in production."
        )
        return False
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


async def _read_raw_body(request: Request) -> bytes:
    return await request.body()


@router.post(
    "/respond",
    response_model=ChatwootCustomToolResponse,
    summary="Captain Custom Tool endpoint",
    description=(
        "Synchronous endpoint for Chatwoot Captain Custom Tools. Chatwoot "
        "POSTs the tool request, AI Hub returns the AI reply in the same "
        "HTTP response. Used for AI Assistant auto-reply and Co-Pilot draft."
    ),
)
async def respond_custom_tool(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None, alias="X-Chatwoot-Signature"),
) -> ChatwootCustomToolResponse:
    raw = await _read_raw_body(request)
    if not _verify_signature(raw, x_chatwoot_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chatwoot-Signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    try:
        req = ChatwootCustomToolRequest.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    service = request.app.state.ai_service
    result = await chatwoot_integration.process_custom_tool(service, req)
    return ChatwootCustomToolResponse(**result)


@router.post(
    "/agent_bot",
    response_model=ChatwootAgentBotResponse,
    summary="AgentBot webhook",
    description=(
        "Async endpoint for Chatwoot AgentBot. Chatwoot POSTs the incoming "
        "customer message, AI Hub processes it and POSTs the reply back to "
        "the conversation.message_url. Returns 200 immediately with a "
        "queued/processed status so Chatwoot doesn't retry."
    ),
)
async def agent_bot_webhook(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None, alias="X-Chatwoot-Signature"),
) -> ChatwootAgentBotResponse:
    raw = await _read_raw_body(request)
    if not _verify_signature(raw, x_chatwoot_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chatwoot-Signature")

    try:
        payload_dict = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    try:
        payload = ChatwootAgentBotPayload.model_validate(payload_dict)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    service = request.app.state.ai_service
    result = await chatwoot_integration.process_agent_bot(service, payload)

    if result is None:
        # Outgoing message or empty — skip, don't callback
        return ChatwootAgentBotResponse(status="skipped", reason="not_incoming_or_empty")

    # Send reply back to Chatwoot via the message_url
    msg_url = payload.conversation.message_url
    if not msg_url:
        logger.warning(
            "Chatwoot AgentBot webhook missing message_url for conversation_id=%s; "
            "reply not delivered",
            payload.conversation.id,
        )
        return ChatwootAgentBotResponse(
            status="processed",
            reason="no_message_url_set_reply_dropped",
        )

    chatwoot_token = os.environ.get("CHATWOOT_API_TOKEN", "")
    if not chatwoot_token:
        logger.error(
            "CHATWOOT_API_TOKEN not configured; cannot send reply back to Chatwoot "
            "for conversation_id=%s",
            payload.conversation.id,
        )
        return ChatwootAgentBotResponse(
            status="processed",
            reason="no_api_token_reply_dropped",
        )

    # Fire-and-forget; Chatwoot webhook already returned 200 to Chatwoot
    async def _send() -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    msg_url,
                    json={"content": result["reply"]},
                    headers={
                        "Content-Type": "application/json",
                        "api_access_token": chatwoot_token,
                    },
                )
                logger.info(
                    "Chatwoot AgentBot reply sent conversation_id=%s len=%d",
                    payload.conversation.id,
                    len(result["reply"]),
                )
        except Exception:
            logger.exception(
                "Failed to send Chatwoot reply conversation_id=%s",
                payload.conversation.id,
            )

    # Don't await — return immediately to Chatwoot
    import asyncio
    asyncio.create_task(_send())
    return ChatwootAgentBotResponse(status="queued")


@router.get("/health", summary="Chatwoot integration health check")
async def health() -> dict[str, Any]:
    """Quick health probe for Chatwoot to verify AI Hub is reachable."""
    return {
        "status": "ok",
        "webhook_secret_configured": bool(_get_webhook_secret()),
        "api_token_configured": bool(os.environ.get("CHATWOOT_API_TOKEN")),
    }


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #1: product_lookup
# ──────────────────────────────────────────────────────────────────────

@router.post(
    "/tools/product_lookup",
    response_model=ProductLookupResponse,
    summary="RAG product/policy/promotion search",
    description=(
        "Captain calls this when the customer asks about a specific product, "
        "policy, or promotion. Returns up to `limit` results from the RAG "
        "knowledge base, ranked by semantic + token similarity. Reuses the "
        "same fanpage/policies/promotions cards seeded for the fanpage project."
    ),
)
async def tool_product_lookup(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None, alias="X-Chatwoot-Signature"),
) -> ProductLookupResponse:
    raw = await _read_raw_body(request)
    if not _verify_signature(raw, x_chatwoot_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chatwoot-Signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e
    try:
        req = ProductLookupRequest.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    service = request.app.state.ai_service
    result = await chatwoot_integration.product_lookup(
        service=service,
        query=req.query,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
        knowledge_domain=req.knowledge_domain,
        limit=req.limit,
    )
    return ProductLookupResponse(**result)


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #2: order_status (STUB)
# ──────────────────────────────────────────────────────────────────────

@router.post(
    "/tools/order_status",
    response_model=OrderStatusResponse,
    summary="Order status lookup (STUB — wire to your order backend)",
    description=(
        "Captain calls this when the customer asks about an order. Currently "
        "a STUB that returns a structured 'not_configured' response. For "
        "production, replace `order_status_lookup` in chatwoot_integration.py "
        "with a call to your order backend (Shopify, custom DB, ERP, etc.)."
    ),
)
async def tool_order_status(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None, alias="X-Chatwoot-Signature"),
) -> OrderStatusResponse:
    raw = await _read_raw_body(request)
    if not _verify_signature(raw, x_chatwoot_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chatwoot-Signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e
    try:
        req = OrderStatusRequest.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    result = await chatwoot_integration.order_status(
        order_id=req.order_id,
        contact_email=req.contact_email,
        tenant_id=req.tenant_id,
    )
    return OrderStatusResponse(**result)


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #3: escalate_human
# ──────────────────────────────────────────────────────────────────────

@router.post(
    "/tools/escalate_human",
    response_model=EscalateHumanResponse,
    summary="Log escalation + return ticket_id",
    description=(
        "Captain calls this when the customer needs a human agent. AI Hub "
        "writes an event to the escalation_events table (auto-created on "
        "first use) and returns a ticket_id. Production should also push "
        "a notification (Slack, email) here — currently just logs."
    ),
)
async def tool_escalate_human(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None, alias="X-Chatwoot-Signature"),
) -> EscalateHumanResponse:
    raw = await _read_raw_body(request)
    if not _verify_signature(raw, x_chatwoot_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Chatwoot-Signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e
    try:
        req = EscalateHumanRequest.model_validate(payload)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {e}") from e

    result = await chatwoot_integration.escalate_human(
        conversation_id=req.conversation_id,
        reason=req.reason,
        contact_id=req.contact_id,
        contact_name=req.contact_name,
        priority=req.priority,
        tenant_id=req.tenant_id,
        project_id=req.project_id,
    )
    return EscalateHumanResponse(**result)
