"""Chatwoot ↔ AI Hub integration service.

Maps Chatwoot Captain Custom Tool and AgentBot payloads into AI Hub
ChatRequest, calls the existing AI service, and formats the response back.

Also implements 3 specialised Custom Tool endpoints (product_lookup,
order_status, escalate_human) that Captain can call declaratively.

The service is stateless except for the escalate_human tool which writes
to the ``escalation_events`` table (lazy table — created on first use).

Tenant mapping strategy (per Chatwoot account):
- The X-API-KEY header authenticates the AI Hub request
- The Chatwoot account.id determines which AI Hub tenant_id to use
- Optional explicit tenant_id / project_id in payload override
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.core.database import get_db_connection
from app.models.chat import ChatRequest, Message
from app.models.chatwoot import (
    ChatwootAgentBotPayload,
    ChatwootCustomToolRequest,
)
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


# Default project_id for Chatwoot-driven traffic when payload doesn't override.
# Set to "fanpage" by default so the seeded fanpage RAG knowledge cards
# (products, policies, promotions) are used automatically. Customers can
# override via the `project_id` field in the payload.
DEFAULT_CHATWOOT_PROJECT = "fanpage"


def _resolve_tenant_id(account_id: int | None, override: str | None) -> str:
    """Map Chatwoot account_id → AI Hub tenant_id.

    Convention: prefix with 'cw_' to avoid collisions with regular tenants.
    Override wins if provided. Falls back to 'cw_default' if no account_id.
    """
    if override:
        return override
    if account_id is not None:
        return f"cw_{account_id}"
    return "cw_default"


def _resolve_session_id(conversation_id: int | None, override: str | None) -> str | None:
    """Map Chatwoot conversation.id → AI Hub session_id (string)."""
    if override:
        return override
    if conversation_id is not None:
        return f"cw-conv-{conversation_id}"
    return None


def _resolve_user_name(contact: Any, sender: Any) -> str:
    """Build a stable user_name from contact/sender context.

    Format: cw_contact_{id} or cw_{name} (sanitized).
    """
    if contact is not None and getattr(contact, "id", None):
        return f"cw_contact_{contact.id}"
    if sender is not None and getattr(sender, "id", None):
        kind = getattr(sender, "type", "user") or "user"
        return f"cw_{kind}_{sender.id}"
    if contact is not None and getattr(contact, "name", None):
        # Sanitize: alnum + underscore only
        safe = "".join(c if c.isalnum() else "_" for c in (contact.name or "")[:32])
        return f"cw_named_{safe}" or "cw_unknown"
    return "cw_anonymous"


def _last_user_message_text(messages: list[Any]) -> str | None:
    """Pick the latest user message from a list of {role, content} dicts or
    ChatwootMessage objects.
    """
    for msg in reversed(messages or []):
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role == "user":
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
            if content:
                return str(content)
    return None


def custom_tool_to_chat_request(req: ChatwootCustomToolRequest) -> ChatRequest:
    """Map a Captain Custom Tool request → AI Hub ChatRequest.

    The last user message in `messages` becomes `user_message`. Earlier messages
    become `history` (with content rewritten as Message list).
    """
    user_text = _last_user_message_text(req.messages) or ""
    if not user_text:
        # Defensive: never let empty message reach ai_service
        user_text = "(empty)"

    history: list[Message] = []
    # Take all but the last user message as history
    last_user_seen = False
    for msg in req.messages or []:
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)
        if role == "user" and not last_user_seen:
            # Skip the last user message — it's the current question
            last_user_seen = True
            continue
        if role in ("user", "assistant", "system") and content:
            history.append(Message(role=role, content=str(content)))

    tenant_id = _resolve_tenant_id(
        req.account.id if req.account else None,
        req.tenant_id,
    )
    user_name = _resolve_user_name(req.contact, None)
    session_id = _resolve_session_id(
        req.conversation.id if req.conversation else None,
        None,
    )

    return ChatRequest(
        project_id=req.project_id or DEFAULT_CHATWOOT_PROJECT,
        tenant_id=tenant_id,
        user_name=user_name,
        user_message=user_text,
        history=history,
        session_id=session_id,
        model_mode="lite",
        enable_search=False,  # Custom Tool scenarios control their own RAG via tool params
    )


def agent_bot_to_chat_request(payload: ChatwootAgentBotPayload) -> ChatRequest | None:
    """Map an AgentBot webhook payload → AI Hub ChatRequest.

    Returns None if the message is outgoing (AI already replied, no need to
    re-process) or if the message is empty.
    """
    msg = payload.message
    if msg.message_type != "incoming":
        return None
    if not msg.content or not msg.content.strip():
        return None

    tenant_id = _resolve_tenant_id(
        payload.conversation.account_id,
        payload.tenant_id,
    )
    user_name = _resolve_user_name(None, payload.sender)
    session_id = _resolve_session_id(
        payload.conversation.id,
        None,
    )

    return ChatRequest(
        project_id=payload.project_id or DEFAULT_CHATWOOT_PROJECT,
        tenant_id=tenant_id,
        user_name=user_name,
        user_message=msg.content,
        history=[],
        session_id=session_id,
        model_mode="lite",
        enable_search=False,
    )


async def process_custom_tool(
    service: AIService,
    req: ChatwootCustomToolRequest,
) -> dict[str, Any]:
    """Process a Captain Custom Tool request and return response dict.

    Returns a dict (not ChatwootCustomToolResponse) so the caller can
    serialize with model_dump() and inject optional fields.
    """
    chat_req = custom_tool_to_chat_request(req)
    try:
        resp = await service.chat(chat_req)
        return {
            "response": resp.content or "",
            "model": resp.model,
            "tokens_in": (resp.usage or {}).get("prompt_tokens") if resp.usage else None,
            "tokens_out": (resp.usage or {}).get("completion_tokens") if resp.usage else None,
            "latency_ms": resp.latency_ms,
        }
    except Exception as exc:
        logger.exception("Chatwoot Custom Tool chat failed: %r", exc)
        return {
            "response": "Xin lỗi, AI đang gặp sự cố. Vui lòng thử lại sau hoặc chuyển cho nhân viên.",
            "model": None,
            "tokens_in": None,
            "tokens_out": None,
            "latency_ms": None,
        }


async def process_agent_bot(
    service: AIService,
    payload: ChatwootAgentBotPayload,
) -> dict[str, str] | None:
    """Process an AgentBot webhook payload. Returns reply text or None to skip.

    Returns dict with 'reply' (text) and 'session_id' so the caller can
    POST the reply back to Chatwoot via the conversation.message_url.
    """
    chat_req = agent_bot_to_chat_request(payload)
    if chat_req is None:
        return None
    try:
        resp = await service.chat(chat_req)
        return {
            "reply": resp.content or "",
            "session_id": resp.session_id or "",
        }
    except Exception as exc:
        logger.exception("Chatwoot AgentBot chat failed: %r", exc)
        return {
            "reply": "Xin lỗi, AI đang gặp sự cố. Vui lòng thử lại sau.",
            "session_id": "",
        }


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #1: product_lookup
# ──────────────────────────────────────────────────────────────────────

async def product_lookup(
    service: AIService,
    query: str,
    tenant_id: str | None,
    project_id: str | None,
    knowledge_domain: str | None,
    limit: int,
) -> dict[str, Any]:
    """Search the RAG knowledge base for products/policies/promotions.

    Reuses the existing knowledge_retrieval_service if available. Falls
    back to "not found" if RAG is disabled or the knowledge service is
    not initialised (e.g. in unit tests without DB).
    """
    tenant = tenant_id or "cw_default"
    project = project_id or DEFAULT_CHATWOOT_PROJECT

    retrieval = getattr(service, "_knowledge_retrieval", None)
    if retrieval is None:
        logger.warning(
            "knowledge_retrieval not initialised on AIService; "
            "product_lookup returns empty (likely test or RAG disabled)"
        )
        return {"found": False, "query": query, "products": []}

    try:
        results = retrieval.search(
            tenant_id=tenant,
            project_id=project,
            query=query,
            knowledge_domain=knowledge_domain,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("product_lookup retrieval failed: %r", exc)
        return {"found": False, "query": query, "products": []}

    products = [
        {
            "title": r.title,
            "summary": r.summary,
            "content": r.content,
            "score": float(r.score),
            "knowledge_domain": r.knowledge_domain,
            "tags": r.tags or [],
        }
        for r in results
    ]
    return {
        "found": len(products) > 0,
        "query": query,
        "products": products,
    }


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #2: order_status (STUB — wire to your order backend)
# ──────────────────────────────────────────────────────────────────────

async def order_status(
    order_id: str,
    contact_email: str | None,
    tenant_id: str | None,
) -> dict[str, Any]:
    """Return order status. STUB: AI Hub has no orders table.

    For production: replace this function with a real call to your
    order backend (Shopify GraphQL, custom DB, ERP API, etc.). The
    response shape is what Captain expects:

        {
          "found": bool,
          "order_id": str,
          "status": "pending" | "shipped" | "delivered" | "cancelled" | "not_configured",
          "details": { ... },
          "message": "Human-readable explanation"
        }

    Example production hook (Shopify):
        shopify_client = ShopifyClient(...)
        order = shopify_client.get_order(order_id)
        if not order: return {"found": False, "status": "not_found", ...}
        return {"found": True, "status": order.fulfillment_status, ...}
    """
    logger.info(
        "order_status STUB called order_id=%s tenant=%s",
        order_id, tenant_id or "cw_default",
    )
    return {
        "found": False,
        "order_id": order_id,
        "status": "not_configured",
        "details": {
            "stub_reason": "AI Hub does not have an orders table. "
                           "Wire order_status_lookup() in chatwoot_integration.py "
                           "to your order backend (Shopify, ERP, etc.)",
            "request_email": contact_email,
        },
        "message": (
            "Hệ thống AI chưa được kết nối với hệ thống đơn hàng. "
            "Vui lòng chuyển cho nhân viên kiểm tra đơn hàng #"
            f"{order_id}."
        ),
    }


# ──────────────────────────────────────────────────────────────────────
# Custom Tool #3: escalate_human (writes to DB)
# ──────────────────────────────────────────────────────────────────────

def _ensure_escalation_table() -> None:
    """Create the escalation_events table if it doesn't exist.

    Lazy creation — runs on first escalate call. Idempotent.
    """
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS escalation_events (
                id              BIGSERIAL PRIMARY KEY,
                ticket_id       TEXT NOT NULL UNIQUE,
                tenant_id       TEXT NOT NULL,
                project_id      TEXT NOT NULL,
                conversation_id BIGINT NOT NULL,
                contact_id      BIGINT,
                contact_name    TEXT,
                reason          TEXT NOT NULL,
                priority        TEXT NOT NULL DEFAULT 'medium',
                status          TEXT NOT NULL DEFAULT 'open',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at     TIMESTAMPTZ
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_escalation_tenant_conv "
            "ON escalation_events (tenant_id, conversation_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_escalation_status "
            "ON escalation_events (tenant_id, status) WHERE status = 'open'"
        )
        conn.commit()


async def escalate_human(
    conversation_id: int,
    reason: str,
    contact_id: int | None,
    contact_name: str | None,
    priority: str,
    tenant_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    """Log an escalation event and return a ticket_id.

    The ticket_id is what Captain (and the agent inbox) should reference.
    AI Hub creates the event in the escalation_events table; production
    would also push a notification (Slack, email) here.
    """
    tenant = tenant_id or "cw_default"
    project = project_id or DEFAULT_CHATWOOT_PROJECT
    ticket_id = f"CW-ESC-{uuid.uuid4().hex[:8].upper()}"

    # Estimated response time by priority
    eta_by_priority = {
        "urgent": 5,
        "high": 15,
        "medium": 30,
        "low": 60,
    }
    eta = eta_by_priority.get(priority, 30)

    # Persistence
    try:
        _ensure_escalation_table()
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO escalation_events
                  (ticket_id, tenant_id, project_id, conversation_id,
                   contact_id, contact_name, reason, priority)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (ticket_id, tenant, project, conversation_id,
                 contact_id, contact_name, reason, priority),
            )
            conn.commit()
        logger.info(
            "Escalation logged ticket_id=%s tenant=%s conv=%d priority=%s",
            ticket_id, tenant, conversation_id, priority,
        )
    except Exception as exc:
        # Don't fail the request if DB is unreachable; return ticket_id
        # anyway so the customer gets a response. Log for ops.
        logger.exception("Failed to persist escalation_event: %r", exc)

    return {
        "escalated": True,
        "ticket_id": ticket_id,
        "assigned_agent": None,  # production: round-robin assign
        "estimated_response_minutes": eta,
        "message": (
            f"Đã chuyển yêu cầu cho nhân viên hỗ trợ (mã: {ticket_id}). "
            f"Nhân viên sẽ phản hồi trong khoảng {eta} phút. "
            "Cảm ơn bạn đã kiên nhẫn!"
        ),
    }
