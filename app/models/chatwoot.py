"""Pydantic models for Chatwoot integration.

Two request shapes supported:

1. **Captain Custom Tool** (synchronous, returns in HTTP response):
   - Chatwoot Captain calls a declared HTTP tool URL, AI Hub responds with JSON.
   - Used for "AI Assistant auto-reply" and "Co-Pilot draft" modes.
   - Path: POST /v1/integrations/chatwoot/respond

2. **AgentBot webhook** (async, AI Hub calls back to Chatwoot to send message):
   - Chatwoot AgentBot posts incoming customer message to AI Hub.
   - AI Hub processes and POSTs reply to Chatwoot's `message_url`.
   - Path: POST /v1/integrations/chatwoot/agent_bot

Reference: https://www.chatwoot.com/developers/api/
            https://www.chatwoot.com/blog/captain-custom-tools/
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────
# Captain Custom Tool format (synchronous)
# ──────────────────────────────────────────────────────────────────────

class ChatwootMessage(BaseModel):
    """One message in conversation history."""
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)


class ChatwootContact(BaseModel):
    """Contact (customer) context. Optional — used for memory/personalization."""
    id: int | None = None
    name: str | None = None
    email: str | None = None
    phone_number: str | None = None


class ChatwootAccount(BaseModel):
    """Chatwoot account (tenant) context. account_id is the key AI Hub maps to tenant_id."""
    id: int
    name: str | None = None


class ChatwootConversation(BaseModel):
    """Conversation metadata. conversation.id is mapped to AI Hub session_id."""
    id: int
    display_id: int | None = None
    status: str | None = None  # open, resolved, pending, snoozed


class ChatwootCustomToolRequest(BaseModel):
    """Captain Custom Tool request payload.

    Chatwoot Captain sends a declared tool URL with this shape. AI Hub returns
    `response` (the AI reply) in the same HTTP call.
    """
    messages: list[ChatwootMessage] = Field(default_factory=list)
    conversation: ChatwootConversation | None = None
    contact: ChatwootContact | None = None
    account: ChatwootAccount | None = None
    # Optional override: explicit project_id (AI Hub) and tenant_id
    project_id: str | None = None
    tenant_id: str | None = None


class ChatwootCustomToolResponse(BaseModel):
    """Captain Custom Tool response.

    `response` is the AI reply that Captain will surface in the conversation.
    """
    response: str
    # Optional metadata (not used by Chatwoot but helpful for debugging)
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: float | None = None


# ──────────────────────────────────────────────────────────────────────
# AgentBot webhook format (async, AI Hub calls back to Chatwoot)
# ──────────────────────────────────────────────────────────────────────

class ChatwootAgentBotMessage(BaseModel):
    """Single message from Chatwoot AgentBot webhook."""
    id: int | None = None
    content: str = ""
    message_type: Literal["incoming", "outgoing"] = "incoming"
    created_at: str | None = None


class ChatwootAgentBotSender(BaseModel):
    """Who sent the message (contact or agent)."""
    id: int | None = None
    name: str | None = None
    type: Literal["contact", "agent", "agent_bot"] | None = None


class ChatwootAgentBotConversation(BaseModel):
    id: int
    account_id: int | None = None
    inbox_id: int | None = None
    status: str | None = None
    # The URL AI Hub should POST to in order to send a reply back to this conversation
    message_url: str | None = None


class ChatwootAgentBotPayload(BaseModel):
    """The envelope Chatwoot sends to AgentBot webhook URLs."""
    event: str = "message_created"
    message: ChatwootAgentBotMessage
    conversation: ChatwootAgentBotConversation
    sender: ChatwootAgentBotSender | None = None
    account: dict | None = None
    # Optional override
    project_id: str | None = None
    tenant_id: str | None = None


class ChatwootAgentBotResponse(BaseModel):
    """Response to AgentBot webhook. 200 OK with empty body is enough — the
    actual reply is sent via callback to conversation.message_url.
    """
    status: Literal["queued", "processed", "skipped"] = "queued"
    reason: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Captain Custom Tools — three specialised endpoints
# ──────────────────────────────────────────────────────────────────────
#
# Each is configured as a separate Custom Tool in Chatwoot Captain. Captain
# decides which tool to call based on the assistant's guardrails. All three
# share the same auth (X-API-KEY) and tenant resolution as the main
# /respond endpoint.


class ProductLookupRequest(BaseModel):
    """Captain calls this when the customer asks about a specific product
    (e.g. "Tell me about Serum Vitamin C", "What's the price of BHA toner?").

    Returns up to ``limit`` products from the RAG knowledge base, ranked by
    semantic + token-overlap similarity. AI Hub searches the same cards
    seeded for the fanpage project (products/policies/promotions domains).
    """
    query: str = Field(min_length=1, max_length=500, description="Product name or keyword (Vietnamese supported)")
    tenant_id: str | None = Field(default=None, description="Override tenant_id (else derived from account)")
    project_id: str | None = Field(default=None, description="Override project_id (else 'fanpage')")
    knowledge_domain: str | None = Field(
        default=None,
        description="Filter to a domain ('products', 'policies', 'promotions'). None = search all.",
    )
    limit: int = Field(default=3, ge=1, le=10)


class ProductResult(BaseModel):
    title: str
    summary: str
    content: str
    score: float
    knowledge_domain: str | None = None
    tags: list[str] = Field(default_factory=list)


class ProductLookupResponse(BaseModel):
    found: bool
    query: str
    products: list[ProductResult] = Field(default_factory=list)


class OrderStatusRequest(BaseModel):
    """Captain calls this when the customer asks about an order
    (e.g. "What's the status of order #12345?", "Khi nào đơn hàng tôi giao?").

    NOTE: AI Hub does NOT have an orders table. This endpoint is a STUB
    that returns a structured "not_configured" response so Captain can
    surface a friendly fallback. For production, wire to your real order
    backend (Shopify, custom DB, etc.) and replace the stub in
    `chatwoot_integration.order_status_lookup`.
    """
    order_id: str = Field(min_length=1, max_length=64, description="Order ID, e.g. 'ORD-12345' or '12345'")
    contact_email: str | None = Field(default=None, description="Optional: customer email for verification")
    tenant_id: str | None = None


class OrderStatusResponse(BaseModel):
    found: bool
    order_id: str
    status: str  # "pending" | "shipped" | "delivered" | "cancelled" | "not_configured"
    details: dict = Field(default_factory=dict)
    message: str  # Human-readable explanation for the agent / customer


class EscalateHumanRequest(BaseModel):
    """Captain calls this when the customer needs a human agent
    (e.g. complex complaint, refund > $X, explicit "talk to a person").

    AI Hub logs the escalation event to a dedicated table for tracking
    and returns a ticket_id the agent can reference.
    """
    conversation_id: int
    reason: str = Field(min_length=1, max_length=500, description="Why the customer needs a human")
    contact_id: int | None = None
    contact_name: str | None = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    tenant_id: str | None = None
    project_id: str | None = None


class EscalateHumanResponse(BaseModel):
    escalated: bool
    ticket_id: str
    assigned_agent: str | None = None
    estimated_response_minutes: int | None = None
    message: str  # Suggested reply to the customer ("Đã chuyển cho nhân viên, vui lòng đợi ...")

