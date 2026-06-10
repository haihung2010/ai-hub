"""A2A (Agent2Agent) protocol routes.

Two endpoints:

1. **GET /v1/a2a/agent-card** — Discovery. Returns the AgentCard JSON
   manifest describing AI Hub's capabilities, skills, and auth.

2. **POST /v1/a2a/jsonrpc** — JSON-RPC 2.0 endpoint. Handles methods:
   - SendMessage: submit a message, returns Task
   - GetTask: poll task status
   - ListTasks: list active tasks
   - CancelTask: cancel an in-flight task

Streaming (SendStreamingMessage via SSE) is NOT yet implemented — see
docs/integrations/a2a.md for the roadmap.

Security (P0.5, 2026-06-10):

- **Auth**: X-API-KEY is required. The endpoint is NOT public. The
  AgentCard declares this so clients know to send the header.
- **Rate limit**: 60 RPM per X-API-KEY, enforced by the global security
  middleware (Redis sliding window with in-memory fallback). The same
  limit applies to /v1/chat — A2A does not get a privileged quota.
- **Auth failures**: tracked via the AuthFailureTracker; 5 failures
  from a single IP block that IP for 60 seconds (see middleware config).
- **OAuth 2.1**: deferred to a future sprint (P2.1 of the security
  roadmap). A2A's spec already supports JWT bearer tokens in the
  Authorization header, so the migration will not require a breaking
  change for compliant clients.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.models.a2a import (
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcRequest,
    JsonRpcResponse,
    SendMessageRequest,
)
from app.services import a2a_integration

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/a2a", tags=["a2a"])


@router.get("/agent-card", summary="A2A AgentCard discovery")
async def get_agent_card(request: Request) -> dict[str, Any]:
    """Return AI Hub's A2A AgentCard. Clients fetch this first to discover
    capabilities, skills, and auth requirements.

    Path: also served at /.well-known/agent.json for clients that look there
    per the A2A convention.
    """
    base = str(request.base_url).rstrip("/")
    return a2a_integration.build_agent_card(base)


@router.post("/jsonrpc", summary="A2A JSON-RPC 2.0 endpoint")
async def jsonrpc(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> dict[str, Any]:
    """Single JSON-RPC 2.0 endpoint. Dispatches by `method` field.

    Methods:
    - SendMessage → returns Task (with role, status, history, artifacts)
    - GetTask → returns Task by id
    - ListTasks → returns list of active Tasks
    - CancelTask → returns cancelled Task or null
    """
    # X-API-KEY is enforced by global middleware, so if we get here the
    # request is authenticated. We don't double-check here.
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return _err(None, JsonRpcErrorCode.PARSE_ERROR, f"Invalid JSON: {e}")

    try:
        rpc = JsonRpcRequest.model_validate(payload)
    except Exception as e:
        return _err(payload.get("id") if isinstance(payload, dict) else None,
                   JsonRpcErrorCode.INVALID_REQUEST, f"Invalid request: {e}")

    service = request.app.state.ai_service

    try:
        if rpc.method == "SendMessage":
            return await _handle_send_message(rpc, service)
        if rpc.method == "GetTask":
            return _handle_get_task(rpc)
        if rpc.method == "ListTasks":
            return _handle_list_tasks(rpc)
        if rpc.method == "CancelTask":
            return _handle_cancel_task(rpc)
        return _err(rpc.id, JsonRpcErrorCode.METHOD_NOT_FOUND,
                     f"Method not found: {rpc.method}")
    except Exception as exc:
        logger.exception("A2A JSON-RPC handler error: %r", exc)
        return _err(rpc.id, JsonRpcErrorCode.INTERNAL_ERROR, f"Internal error: {exc}")


async def _handle_send_message(rpc: JsonRpcRequest, service) -> dict[str, Any]:
    """SendMessage: build a Task, dispatch to AI Hub, return result."""
    if not rpc.params:
        return _err(rpc.id, JsonRpcErrorCode.INVALID_PARAMS, "Missing params")
    try:
        send_req = SendMessageRequest.model_validate(rpc.params)
    except Exception as e:
        return _err(rpc.id, JsonRpcErrorCode.INVALID_PARAMS, f"Invalid SendMessage params: {e}")

    task = await a2a_integration.send_message(service, send_req)
    return JsonRpcResponse(jsonrpc="2.0", id=rpc.id, result=task).model_dump(exclude_none=True)


def _handle_get_task(rpc: JsonRpcRequest) -> dict[str, Any]:
    """GetTask: retrieve a task by ID."""
    task_id = (rpc.params or {}).get("id")
    if not task_id:
        return _err(rpc.id, JsonRpcErrorCode.INVALID_PARAMS, "Missing params.id")
    task = a2a_integration.get_task(task_id)
    if task is None:
        return _err(rpc.id, JsonRpcErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
    return JsonRpcResponse(jsonrpc="2.0", id=rpc.id, result=task).model_dump(exclude_none=True)


def _handle_list_tasks(rpc: JsonRpcRequest) -> dict[str, Any]:
    """ListTasks: return all active (non-terminal) tasks."""
    tasks = a2a_integration.list_tasks()
    return JsonRpcResponse(
        jsonrpc="2.0", id=rpc.id, result={"tasks": tasks}
    ).model_dump(exclude_none=True)


def _handle_cancel_task(rpc: JsonRpcRequest) -> dict[str, Any]:
    """CancelTask: mark a task as CANCELED. Returns the cancelled task or null."""
    task_id = (rpc.params or {}).get("id")
    if not task_id:
        return _err(rpc.id, JsonRpcErrorCode.INVALID_PARAMS, "Missing params.id")
    task = a2a_integration.cancel_task(task_id)
    if task is None:
        # Could be: not found, or already terminal
        existing = a2a_integration.get_task(task_id)
        if existing is None:
            return _err(rpc.id, JsonRpcErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
        return _err(rpc.id, JsonRpcErrorCode.UNSUPPORTED_OPERATION,
                     f"Task already in terminal state: {existing.get('status', {}).get('state')}")
    return JsonRpcResponse(jsonrpc="2.0", id=rpc.id, result=task).model_dump(exclude_none=True)


def _err(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response."""
    resp = JsonRpcResponse(
        jsonrpc="2.0",
        id=rpc_id,
        error=JsonRpcError(code=code, message=message),
    )
    return resp.model_dump(exclude_none=True)
