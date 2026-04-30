"""Domain exceptions mapped to HTTP responses in app.main."""


class AIHubError(Exception):
    """Base class for all AI Hub errors."""


class ProjectNotFound(AIHubError):
    """Raised when project_id has no matching prompt file."""


class SessionAccessDenied(AIHubError):
    """Raised when a session does not belong to the requested tenant/project/user."""


class OllamaUnavailable(AIHubError):
    """Ollama service is not reachable (connection refused / connect timeout)."""


class VramExhausted(AIHubError):
    """Ollama reported out-of-memory / model load failure."""


class UpstreamTimeout(AIHubError):
    """Upstream provider exceeded the read timeout."""


class UpstreamError(AIHubError):
    """Upstream provider returned a non-2xx, non-classified response."""
