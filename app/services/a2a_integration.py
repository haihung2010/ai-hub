"""A2A (Agent2Agent) service layer for AI Hub.

Maps A2A protocol messages → AI Hub ChatRequest, dispatches via the
existing ai_service.chat(), and shapes the result back as A2A Task +
Artifact objects. Also implements the in-memory task store for
SendMessage / GetTask / ListTasks / CancelTask.

Storage:
- In-memory dict keyed by task ID (UUID4)
- TTL: 1 hour (older tasks purged on access)
- Not persisted — for production swap to Redis or PostgreSQL

Streaming (SendStreamingMessage via SSE) is NOT yet implemented;
SendMessage with blocking=true returns the full task in one shot.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from app.models.a2a import (
    Artifact,
    Message,
    Part,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from app.models.chat import ChatRequest
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


# In-memory task store
_TASKS: dict[str, Task] = {}
_TASKS_LOCK = threading.Lock()
_TASK_TTL_SECONDS = 3600  # 1 hour
_DEFAULT_PROJECT_ID = "fanpage"


def _now() -> float:
    return time.time()


def _purge_old_tasks() -> None:
    """Remove tasks older than TTL. Called on every store access."""
    cutoff = _now() - _TASK_TTL_SECONDS
    with _TASKS_LOCK:
        for tid in list(_TASKS.keys()):
            task = _TASKS[tid]
            # Tasks without a timestamp can't be aged — keep them.
            ts = task.history[0].parts[0].text if task.history and task.history[0].parts else None
            if ts and hasattr(task, "_created_at"):
                if task._created_at < cutoff:
                    del _TASKS[tid]


def _resolve_tenant(req: SendMessageRequest) -> str:
    """Derive AI Hub tenant_id from A2A message context.

    Convention: use context_id if it looks like a Chatwoot account_id
    (cw_<id>), otherwise default to "a2a_default".
    """
    if req.context_id:
        return f"cw_{req.context_id}" if not req.context_id.startswith("cw_") else req.context_id
    return "a2a_default"


def _resolve_user(req: SendMessageRequest) -> str:
    """Derive a stable user_name for memory continuity."""
    if req.context_id:
        return f"a2a_{req.context_id}"
    return f"a2a_{req.id or 'anon'}"


def _part_to_text(parts: list[Part]) -> str:
    """Extract the first text part from a list. Used to build user_message."""
    for p in parts:
        if isinstance(p, TextPart) and p.text:
            return p.text
    return ""


def _text_to_part(text: str) -> TextPart:
    """Wrap text in a TextPart for A2A response."""
    return TextPart(text=text)


def build_chat_request(req: SendMessageRequest) -> ChatRequest:
    """Convert A2A SendMessageRequest → AI Hub ChatRequest.

    - Last user message text becomes user_message
    - Earlier messages become history (with role mapping: agent→assistant)
    - context_id → tenant_id (cw_<n> convention)
    - task id → session_id (so memory persists across turns)
    """
    user_text = _part_to_text(req.message.parts)
    if not user_text:
        user_text = "(empty)"

    history = []
    # If continuation (req.id is set), include earlier agent messages as history
    if req.id and req.id in _TASKS:
        prior = _TASKS[req.id]
        for msg in prior.history:
            if msg.role == "user":
                history.append({"role": "user", "content": _part_to_text(msg.parts)})
            elif msg.role == "agent":
                history.append({"role": "assistant", "content": _part_to_text(msg.parts)})

    tenant_id = _resolve_tenant(req)
    user_name = _resolve_user(req)

    # Build Pydantic Message list for ChatRequest
    from app.models.chat import Message as ChatMessage
    chat_history = [ChatMessage(role=h["role"], content=h["content"]) for h in history]

    return ChatRequest(
        project_id=_DEFAULT_PROJECT_ID,
        tenant_id=tenant_id,
        user_name=user_name,
        user_message=user_text,
        history=chat_history,
        session_id=req.id,  # continuation via task id
        model_mode="lite",
        enable_search=False,
    )


async def send_message(
    service: AIService,
    req: SendMessageRequest,
) -> Task:
    """Process SendMessage: dispatch to AI Hub, build A2A Task response.

    Behavior (blocking=true, the A2A default):
    - Creates a Task in `submitted` state
    - Calls ai_service.chat() to get the response
    - Updates Task to `working` then `completed` (or `failed`)
    - Returns the final Task with the agent response in history + artifacts
    """
    task_id = req.id or f"a2a-{uuid.uuid4().hex[:12]}"
    context_id = req.context_id or f"ctx-{uuid.uuid4().hex[:8]}"

    # Create initial Task in submitted state
    initial_task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.SUBMITTED),
        history=[req.message],
        artifacts=[],
    )
    initial_task._created_at = _now()  # type: ignore[attr-defined]
    with _TASKS_LOCK:
        _TASKS[task_id] = initial_task

    # Dispatch to AI Hub
    chat_req = build_chat_request(req)
    try:
        # Update to working
        with _TASKS_LOCK:
            _TASKS[task_id].status = TaskStatus(state=TaskState.WORKING)
        resp = await service.chat(chat_req)

        # Build the agent reply message
        agent_message = Message(
            role="agent",
            parts=[_text_to_part(resp.content or "(empty response)")],
            context_id=context_id,
        )

        # Append to history
        with _TASKS_LOCK:
            _TASKS[task_id].history.append(agent_message)
            _TASKS[task_id].status = TaskStatus(
                state=TaskState.COMPLETED,
                message=agent_message,
            )
            # Save the response as an Artifact
            _TASKS[task_id].artifacts.append(
                Artifact(
                    name="assistant_reply",
                    description="AI Hub chat completion",
                    parts=[_text_to_part(resp.content or "(empty response)")],
                    index=0,
                )
            )
            result = _TASKS[task_id]
    except Exception as exc:
        logger.exception("A2A send_message failed: %r", exc)
        with _TASKS_LOCK:
            _TASKS[task_id].status = TaskStatus(
                state=TaskState.FAILED,
                message=Message(
                    role="agent",
                    parts=[_text_to_part(f"AI Hub error: {exc!s}")],
                    context_id=context_id,
                ),
            )
            result = _TASKS[task_id]

    return result.model_dump(exclude={"_created_at"} if hasattr(result, "_created_at") else None)


def get_task(task_id: str) -> Task | None:
    """Retrieve a task by ID. Purges old tasks first."""
    _purge_old_tasks()
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if task is None:
        return None
    return task.model_dump(exclude={"_created_at"} if hasattr(task, "_created_at") else None)


def list_tasks() -> list[Task]:
    """List all non-terminal tasks. Purges old tasks first."""
    _purge_old_tasks()
    with _TASKS_LOCK:
        tasks = [t for t in _TASKS.values() if t.status.state not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}]
    return [t.model_dump(exclude={"_created_at"} if hasattr(t, "_created_at") else None) for t in tasks]


def cancel_task(task_id: str) -> Task | None:
    """Mark a task as CANCELED. Returns None if not found or already terminal."""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None or task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}:
            return None
        task.status = TaskStatus(state=TaskState.CANCELED)
        result = task
    return result.model_dump(exclude={"_created_at"} if hasattr(result, "_created_at") else None)


def build_agent_card(server_base_url: str) -> dict[str, Any]:
    """Build the A2A AgentCard for AI Hub.

    Documents the 3 main skills (mapped to existing Chatwoot Custom Tools):
    - fanpage_chat: general chat (any Vietnamese query)
    - product_lookup: RAG product search
    - order_status: order status (STUB)
    - escalate_human: hand off to human
    """
    from app.models.a2a import (
        AgentCard,
        AgentProvider,
        AgentSkill,
        AgentAuthentication,
        AgentCapabilities,
    )

    card = AgentCard(
        name="AI Hub Fanpage Assistant",
        description=(
            "Local-first multilingual AI assistant for Vietnamese customer support. "
            "Backed by Gemma 4 12B Q4 / E2B Q4 on a single RTX 5060 Ti 16GB. "
            "RAG over fanpage knowledge cards, StructMem for cross-session memory, "
            "adaptive routing easy→E2B-bg / med→E4B / hard→12B."
        ),
        version="1.0.0",
        provider=AgentProvider(
            organization="AI Hub",
            url="https://github.com/haihung2010/ai-hub",
        ),
        url=f"{server_base_url.rstrip('/')}/v1/a2a/jsonrpc",
        preferred_transport="http+jsonrpc",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        authentication=AgentAuthentication(
            schemes=["apiKey"],
            credentials="X-API-KEY header (use the same key as /v1/chat)",
        ),
        skills=[
            AgentSkill(
                id="fanpage_chat",
                name="Fanpage Chat (General)",
                description=(
                    "Conversational AI in Vietnamese. Answers product questions, "
                    "policies, and general fanpage inquiries. Uses RAG over "
                    "fanpage/policies/promotions knowledge cards."
                ),
                examples=[
                    "Giá sản phẩm A là bao nhiêu?",
                    "Chính sách đổi trả trong 7 ngày như thế nào?",
                ],
                tags=["chat", "vietnamese", "fanpage", "rag"],
            ),
            AgentSkill(
                id="product_lookup",
                name="Product Catalog Search",
                description=(
                    "Search the fanpage product catalog. Returns up to 3 matching "
                    "products with title, summary, content, and relevance score."
                ),
                examples=[
                    "Tìm serum vitamin C",
                    "Có sản phẩm nào dưỡng ẩm cho da khô?",
                ],
                tags=["product", "search", "rag", "ecommerce"],
            ),
            AgentSkill(
                id="escalate_human",
                name="Escalate to Human",
                description=(
                    "Log an escalation event and return a ticket_id for handoff "
                    "to a human agent. Priority-based ETA: urgent=5min, high=15min, "
                    "medium=30min, low=60min."
                ),
                examples=[
                    "Customer wants a refund over 500K VND",
                    "Customer threatens legal action",
                ],
                tags=["escalation", "handoff", "support"],
            ),
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
    )
    return card.model_dump(by_alias=True, exclude_none=True)
