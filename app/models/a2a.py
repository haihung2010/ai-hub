"""A2A (Agent2Agent) Protocol Pydantic models.

Implements the A2A v1.0.0 data model + JSON-RPC 2.0 envelope. AI Hub acts
as an A2A Server — exposing its capabilities via AgentCard, accepting
SendMessage / GetTask / ListTasks / CancelTask requests, and producing
Task objects with the standard state machine.

Reference:
- https://a2a-protocol.org/latest/specification/
- https://github.com/a2aproject/a2a-samples (Python SDK + clients)

Layer 1 (data model) and Layer 3 (JSON-RPC binding) are covered here.
Streaming (SSE) is implemented in a future iteration — see docs/a2a.md
for the SendStreamingMessage roadmap.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ConfigDict


# ──────────────────────────────────────────────────────────────────────
# Task state machine
# ──────────────────────────────────────────────────────────────────────


class TaskState(str, Enum):
    """A2A task lifecycle states."""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"


TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
    TaskState.REJECTED,
}


# ──────────────────────────────────────────────────────────────────────
# Part types (content)
# ──────────────────────────────────────────────────────────────────────


class TextPart(BaseModel):
    """Plain text content within a message or artifact."""
    kind: Literal["text"] = "text"
    text: str


class FilePart(BaseModel):
    """File reference (URI or inline bytes). AI Hub only supports URIs (the
    actual file is the client's responsibility to host).
    """
    kind: Literal["file"] = "file"
    file: dict  # {"name": str, "mimeType": str, "uri": str}


class DataPart(BaseModel):
    """Structured JSON data (e.g. function call result, form values)."""
    kind: Literal["data"] = "data"
    data: dict


# Discriminated union — A2A's Part is a tagged union of text/file/data
Part = Annotated[Union[TextPart, FilePart, DataPart], Field(discriminator="kind")]


# ──────────────────────────────────────────────────────────────────────
# Message
# ──────────────────────────────────────────────────────────────────────


class Message(BaseModel):
    """A single turn in a conversation between client and remote agent."""
    model_config = ConfigDict(populate_by_name=True)

    role: Literal["user", "agent"]
    parts: list[Part]
    # Optional context identifier (groups related tasks)
    context_id: str | None = Field(default=None, alias="contextId")


# ──────────────────────────────────────────────────────────────────────
# Artifact
# ──────────────────────────────────────────────────────────────────────


class Artifact(BaseModel):
    """An output of a task — a document, image, or structured data."""
    name: str | None = None
    description: str | None = None
    parts: list[Part]
    # Index of this artifact within the task (multiple artifacts per task)
    index: int = 0


# ──────────────────────────────────────────────────────────────────────
# Task
# ──────────────────────────────────────────────────────────────────────


class TaskStatus(BaseModel):
    """Current state + message of a task."""
    state: TaskState
    message: Message | None = None  # Optional human-readable update


class Task(BaseModel):
    """The fundamental unit of work in A2A. Stateful, progresses through states."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    context_id: str | None = Field(default=None, alias="contextId")
    status: TaskStatus
    history: list[Message] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# SendMessage request/response
# ──────────────────────────────────────────────────────────────────────


class SendMessageConfiguration(BaseModel):
    """Per-request configuration for SendMessage."""
    accepted_output_modes: list[str] = Field(default_factory=lambda: ["text"])
    blocking: bool = True  # If true, wait for the task to complete (or error)


class SendMessageRequest(BaseModel):
    """SendMessage input — the message + optional task ID for continuation."""
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, description="Optional task ID for continuation")
    context_id: str | None = Field(default=None, alias="contextId")
    message: Message
    configuration: SendMessageConfiguration = Field(default_factory=SendMessageConfiguration)


# ──────────────────────────────────────────────────────────────────────
# AgentCard — capability discovery
# ──────────────────────────────────────────────────────────────────────


class AgentSkill(BaseModel):
    """A specific capability the agent can perform. Used for skill-based routing."""
    id: str  # e.g. "fanpage_product_search"
    name: str  # e.g. "Product Catalog Search"
    description: str
    # Optional examples
    examples: list[str] = Field(default_factory=list)
    # Input/output modalities
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])
    # Tags for searchability
    tags: list[str] = Field(default_factory=list)


class AgentProvider(BaseModel):
    """Who provides this agent (org, contact)."""
    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    """Optional features this agent supports (streaming, push notifications)."""
    streaming: bool = False
    push_notifications: bool = False


class AgentAuthentication(BaseModel):
    """Authentication schemes this agent supports. AI Hub uses X-API-KEY."""
    schemes: list[str] = Field(default_factory=lambda: ["apiKey"])
    # For apiKey scheme, where the key goes
    credentials: str | None = None  # e.g. "X-API-KEY header"


class AgentCard(BaseModel):
    """The A2A Server's capability manifest. Clients fetch this first to
    discover what the agent can do and how to authenticate.

    Published at GET /v1/a2a/agent-card (also under .well-known/agent.json
    for clients that look there).
    """
    model_config = ConfigDict(populate_by_name=True)

    name: str  # e.g. "AI Hub Fanpage Assistant"
    description: str
    version: str = "1.0.0"
    provider: AgentProvider | None = None
    # The URL of the JSON-RPC endpoint
    url: str
    preferred_transport: Literal["http+json", "grpc", "http+jsonrpc"] = "http+jsonrpc"
    # Capabilities
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    # Auth
    authentication: AgentAuthentication = Field(default_factory=AgentAuthentication)
    # Skills (this is what clients route against)
    skills: list[AgentSkill] = Field(default_factory=list)
    # Default input/output modes
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])


# ──────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 envelope
# ──────────────────────────────────────────────────────────────────────


class JsonRpcRequest(BaseModel):
    """A2A wire format — JSON-RPC 2.0 request envelope."""
    jsonrpc: Literal["2.0"] = "2.0"
    method: str  # "SendMessage", "GetTask", "ListTasks", "CancelTask"
    params: dict | None = None
    id: int | str | None = None  # request ID for correlation


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 error object (https://www.jsonrpc.org/specification#error_object)."""
    code: int
    message: str
    data: Any | None = None


# Standard JSON-RPC error codes
class JsonRpcErrorCode:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # A2A-specific server errors (reserved range -32000 to -32099)
    TASK_NOT_FOUND = -32001
    CONTENT_TYPE_NOT_SUPPORTED = -32002
    UNSUPPORTED_OPERATION = -32003


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 success response."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    result: dict | None = None
    error: JsonRpcError | None = None
