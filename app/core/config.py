"""Typed application settings loaded from environment (.env supported)."""

from functools import lru_cache
import json

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "https://htechlabsvn.com",
    "https://api-aiserver.htechlabsvn.com",
]
DEFAULT_ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "testserver",
    "api-aiserver.htechlabsvn.com",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    local_provider: str = Field(default="llama_cpp", alias="LOCAL_PROVIDER")
    llama_cpp_base_url: str = Field(default="http://localhost:8080", alias="LLAMA_CPP_BASE_URL")
    llama_cpp_openai_url: str = Field(default="http://localhost:8080/v1", alias="LLAMA_CPP_OPENAI_URL")

    openrouter_enabled: bool = Field(default=False, alias="OPENROUTER_ENABLED")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openai/gpt-oss-20b:free", alias="OPENROUTER_MODEL")
    openrouter_fallback_models: list[str] = Field(default_factory=list, alias="OPENROUTER_FALLBACK_MODELS")
    openrouter_allowed_projects: list[str] = Field(default_factory=lambda: ["test"], alias="OPENROUTER_ALLOWED_PROJECTS")
    openrouter_denied_projects: list[str] = Field(default_factory=lambda: ["vehix"], alias="OPENROUTER_DENIED_PROJECTS")
    openrouter_timeout_seconds: float = Field(default=90.0, alias="OPENROUTER_TIMEOUT_SECONDS")
    external_llm_default_allowed: bool = Field(default=False, alias="EXTERNAL_LLM_DEFAULT_ALLOWED")

    nine_router_enabled: bool = Field(default=False, alias="NINE_ROUTER_ENABLED")
    nine_router_base_url: str = Field(default="http://localhost:20128", alias="NINE_ROUTER_BASE_URL")
    nine_router_api_key: str = Field(default="", alias="NINE_ROUTER_API_KEY")
    nine_router_model: str = Field(default="kr/claude-sonnet-4.5", alias="NINE_ROUTER_MODEL")
    nine_router_allowed_projects: list[str] = Field(default_factory=list, alias="NINE_ROUTER_ALLOWED_PROJECTS")
    nine_router_denied_projects: list[str] = Field(default_factory=list, alias="NINE_ROUTER_DENIED_PROJECTS")

    default_model: str = Field(
        default="local-gemma4-12b-q4-text",
        alias="DEFAULT_MODEL",
    )
    lite_model: str = Field(
        default="local-gemma4-12b-q4-text",
        alias="LITE_MODEL",
    )
    lite_num_ctx: int = Field(default=8192, ge=512, le=131072, alias="LITE_NUM_CTX")
    default_num_ctx: int = Field(default=8192, alias="DEFAULT_NUM_CTX")
    lite_max_history_messages: int = Field(default=20, alias="LITE_MAX_HISTORY_MESSAGES")
    request_timeout_seconds: float = Field(default=60.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_history_messages: int = Field(default=20, alias="MAX_HISTORY_MESSAGES")
    gpu_concurrency: int = Field(default=8, ge=1, alias="GPU_CONCURRENCY")
    hybrid_local_queue_timeout_seconds: float = Field(default=2.0, ge=0, alias="HYBRID_LOCAL_QUEUE_TIMEOUT_SECONDS")
    hybrid_force_cloud_when_locked: bool = Field(default=True, alias="HYBRID_FORCE_CLOUD_WHEN_LOCKED")
    hybrid_force_cloud_for_allowed: bool = Field(default=False, alias="HYBRID_FORCE_CLOUD_FOR_ALLOWED")
    hybrid_long_prompt_char_threshold: int = Field(default=0, ge=0, alias="HYBRID_LONG_PROMPT_CHAR_THRESHOLD")
    hybrid_latency_threshold_ms: float = Field(default=8000.0, gt=0, alias="HYBRID_LATENCY_THRESHOLD_MS")
    hybrid_latency_window: int = Field(default=20, ge=3, le=100, alias="HYBRID_LATENCY_WINDOW")
    cloud_fallback_max_concurrency: int = Field(default=5, ge=1, alias="CLOUD_FALLBACK_MAX_CONCURRENCY")
    ai_max_tokens: int = Field(default=0, ge=0, alias="AI_MAX_TOKENS")
    local_max_tokens: int = Field(default=0, ge=0, alias="LOCAL_MAX_TOKENS")
    openrouter_max_tokens: int = Field(default=0, ge=0, alias="OPENROUTER_MAX_TOKENS")
    # MiniMax M3 cloud fallback (Anthropic-compatible, supports cache_control)
    minimax_enabled: bool = Field(default=False, alias="MINIMAX_ENABLED")
    minimax_api_key: str = Field(default="", alias="MINIMAX_API_KEY")
    minimax_base_url: str = Field(default="https://api.minimax.io/v1", alias="MINIMAX_BASE_URL")
    minimax_model: str = Field(default="MiniMax-M3", alias="MINIMAX_MODEL")
    minimax_timeout_seconds: float = Field(default=90.0, gt=0, alias="MINIMAX_TIMEOUT_SECONDS")
    minimax_max_tokens: int = Field(default=0, ge=0, alias="MINIMAX_MAX_TOKENS")
    minimax_allowed_projects: list[str] = Field(default_factory=list, alias="MINIMAX_ALLOWED_PROJECTS")
    minimax_denied_projects: list[str] = Field(default_factory=list, alias="MINIMAX_DENIED_PROJECTS")
    # MiniMax MCP web search — spawns `minimax-coding-plan-mcp` over stdio JSON-RPC
    minimax_mcp_enabled: bool = Field(default=True, alias="MINIMAX_MCP_ENABLED")
    minimax_mcp_command: str = Field(default="uvx", alias="MINIMAX_MCP_COMMAND")
    minimax_mcp_args: list[str] = Field(
        default_factory=lambda: ["minimax-coding-plan-mcp", "-y"],
        alias="MINIMAX_MCP_ARGS",
    )
    minimax_mcp_timeout_seconds: float = Field(default=8.0, gt=0, alias="MINIMAX_MCP_TIMEOUT_SECONDS")
    minimax_mcp_max_results: int = Field(default=5, ge=1, le=10, alias="MINIMAX_MCP_MAX_RESULTS")
    adaptive_max_tokens_enabled: bool = Field(default=True, alias="ADAPTIVE_MAX_TOKENS_ENABLED")
    adaptive_max_tokens_threshold: int = Field(default=5, ge=1, alias="ADAPTIVE_MAX_TOKENS_THRESHOLD")
    adaptive_max_tokens_cutoff_pct: float = Field(default=0.75, ge=0.1, le=1.0, alias="ADAPTIVE_MAX_TOKENS_CUTOFF_PCT")
    adaptive_max_tokens_severe_pct: float = Field(default=0.50, ge=0.1, le=1.0, alias="ADAPTIVE_MAX_TOKENS_SEVERE_PCT")
    priority_queue_timeout_enabled: bool = Field(default=True, alias="PRIORITY_QUEUE_TIMEOUT_ENABLED")
    load_shed_threshold: int = Field(default=5, ge=0, alias="LOAD_SHED_THRESHOLD")
    ai_top_p: float = Field(default=0.0, ge=0.0, le=1.0, alias="AI_TOP_P")
    provider_call_timeout_seconds: float = Field(default=0.0, ge=0.0, alias="PROVIDER_CALL_TIMEOUT_SECONDS")
    api_key: str = Field(min_length=16, alias="API_KEY")
    rate_limit_per_minute: int = Field(default=5, ge=1, alias="RATE_LIMIT_PER_MINUTE")
    # P1.1: per-tenant rate limit (cumulative across all keys of the
    # same tenant). 200 RPM matches 16 GPU slots with 40% headroom.
    tenant_rate_limit_rpm: int = Field(default=200, ge=1, alias="TENANT_RATE_LIMIT_RPM")
    security_log_file: str = Field(default="security.log", alias="SECURITY_LOG_FILE")
    # Langfuse tracing — when LANGFUSE_PUBLIC_KEY is non-empty, every chat
    # request is traced (latency, token usage, retrieval hits, model id).
    # Leave empty to disable; ai-hub continues to use its own PG usage table
    # for cost/latency accounting regardless of this setting.
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="http://localhost:3000", alias="LANGFUSE_HOST")
    public_health_enabled: bool = Field(default=True, alias="PUBLIC_HEALTH_ENABLED")
    public_docs_enabled: bool = Field(default=True, alias="PUBLIC_DOCS_ENABLED")
    auth_failure_limit: int = Field(default=10, ge=1, alias="AUTH_FAILURE_LIMIT")
    auth_failure_block_seconds: int = Field(default=900, ge=1, alias="AUTH_FAILURE_BLOCK_SECONDS")
    security_pg_audit_enabled: bool = Field(default=True, alias="SECURITY_PG_AUDIT_ENABLED")
    summary_threshold: int = Field(default=20, ge=1, alias="SUMMARY_THRESHOLD")
    summary_context_token_threshold: int = Field(default=4000, ge=1, alias="SUMMARY_CONTEXT_TOKEN_THRESHOLD")
    summary_concurrency: int = Field(default=1, ge=1, alias="SUMMARY_CONCURRENCY")
    summary_model: str = Field(default="local-gemma4-e4b-q4", alias="SUMMARY_MODEL")
    enable_structmem: bool = Field(default=False, alias="ENABLE_STRUCTMEM")
    structmem_extraction_threshold: int = Field(default=8, ge=1, alias="STRUCTMEM_EXTRACTION_THRESHOLD")
    structmem_extraction_model: str = Field(default="local-gemma4-e4b-q4", alias="STRUCTMEM_EXTRACTION_MODEL")
    structmem_consolidation_model: str = Field(default="local-gemma4-e4b-q4", alias="STRUCTMEM_CONSOLIDATION_MODEL")
    structmem_max_episodic: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_EPISODIC")
    structmem_max_semantic: int = Field(default=6, ge=0, alias="STRUCTMEM_MAX_SEMANTIC")
    structmem_max_relational: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_RELATIONAL")
    structmem_max_procedural: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_PROCEDURAL")
    structmem_max_consolidated: int = Field(default=3, ge=0, alias="STRUCTMEM_MAX_CONSOLIDATED")
    structmem_debug_include_trace: bool = Field(default=False, alias="STRUCTMEM_DEBUG_INCLUDE_TRACE")
    enable_failure_risk: bool = Field(default=True, alias="ENABLE_FAILURE_RISK")
    failure_risk_log_only: bool = Field(default=True, alias="FAILURE_RISK_LOG_ONLY")
    failure_risk_enable_actions: bool = Field(default=False, alias="FAILURE_RISK_ENABLE_ACTIONS")
    failure_risk_high_threshold: float = Field(default=0.6, ge=0.0, le=1.0, alias="FAILURE_RISK_HIGH_THRESHOLD")
    failure_risk_medium_threshold: float = Field(default=0.3, ge=0.0, le=1.0, alias="FAILURE_RISK_MEDIUM_THRESHOLD")
    failure_risk_enable_search_action: bool = Field(default=True, alias="FAILURE_RISK_ENABLE_SEARCH_ACTION")
    # Adaptive routing (added 2026-06-07)
    adaptive_routing_enabled: bool = Field(default=True, alias="ADAPTIVE_ROUTING_ENABLED")
    difficulty_easy_threshold: float = Field(default=0.3, ge=0.0, le=1.0, alias="DIFFICULTY_EASY_THRESHOLD")
    difficulty_hard_threshold: float = Field(default=0.6, ge=0.0, le=1.0, alias="DIFFICULTY_HARD_THRESHOLD")
    saturation_12b_degrade_threshold: float = Field(default=0.8, ge=0.0, le=1.0, alias="SATURATION_12B_DEGRADE_THRESHOLD")
    saturation_e4b_degrade_threshold: float = Field(default=0.9, ge=0.0, le=1.0, alias="SATURATION_E4B_DEGRADE_THRESHOLD")
    load_probe_interval_seconds: float = Field(default=1.0, gt=0.0, alias="LOAD_PROBE_INTERVAL_SECONDS")
    load_cache_ttl_seconds: float = Field(default=0.2, gt=0.0, alias="LOAD_CACHE_TTL_SECONDS")
    periodic_summary_cron: str = Field(default="0 */6 * * *", alias="PERIODIC_SUMMARY_CRON")
    periodic_summary_min_tokens: int = Field(default=5000, ge=0, alias="PERIODIC_SUMMARY_MIN_TOKENS")
    enable_knowledge_rag: bool = Field(default=True, alias="ENABLE_KNOWLEDGE_RAG")
    knowledge_max_chunks: int = Field(default=4, ge=0, le=10, alias="KNOWLEDGE_MAX_CHUNKS")
    knowledge_chunk_chars: int = Field(default=2000, ge=500, le=8000, alias="KNOWLEDGE_CHUNK_CHARS")
    knowledge_chunk_overlap_chars: int = Field(default=200, ge=0, le=1000, alias="KNOWLEDGE_CHUNK_OVERLAP_CHARS")
    knowledge_max_card_chars: int = Field(default=100000, ge=1000, alias="KNOWLEDGE_MAX_CARD_CHARS")
    knowledge_embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        alias="KNOWLEDGE_EMBEDDING_MODEL",
    )
    reranker_enabled: bool = Field(default=True, alias="RERANKER_ENABLED")
    reranker_url: str = Field(default="http://127.0.0.1:8082", alias="RERANKER_URL")
    reranker_timeout_seconds: float = Field(default=10.0, gt=0, alias="RERANKER_TIMEOUT_SECONDS")
    background_llama_cpp_openai_url: str = Field(default="http://localhost:8081/v1", alias="BACKGROUND_LLAMA_CPP_OPENAI_URL")
    background_llama_cpp_enabled: bool = Field(default=False, alias="BACKGROUND_LLAMA_CPP_ENABLED")
    background_llama_cpp_parallel: int = Field(default=8, ge=1, alias="BACKGROUND_LLAMA_CPP_PARALLEL")
    ihi_llama_cpp_openai_url: str = Field(default="http://localhost:8083/v1", alias="IHI_LLAMA_CPP_OPENAI_URL")
    ihi_llama_cpp_enabled: bool = Field(default=True, alias="IHI_LLAMA_CPP_ENABLED")
    llama_cpp_nodes: list[str] = Field(default_factory=list, alias="LLAMA_CPP_NODES")
    enable_crew_agents: bool = Field(default=False, alias="ENABLE_CREW_AGENTS")
    crew_model: str = Field(default="local-gemma4-e4b-q4", alias="CREW_MODEL")
    # P0.4: Whisper audio transcription (gated by ENABLE_WHISPER; off by
    # default so the model isn't loaded on instances that don't need it).
    enable_whisper: bool = Field(default=False, alias="ENABLE_WHISPER")
    whisper_model_size: str = Field(default="large-v3-turbo", alias="WHISPER_MODEL_SIZE")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    google_ai_studio_api_key: str = Field(default="", alias="GOOGLE_AI_STUDIO_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    searxng_base_url: str = Field(default="", alias="SEARXNG_BASE_URL")
    allowed_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS),
        alias="ALLOWED_ORIGINS",
    )
    allowed_hosts: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS),
        alias="ALLOWED_HOSTS",
    )
    trusted_proxy_ips: list[str] = Field(default_factory=list, alias="TRUSTED_PROXY_IPS")
    allowed_projects: list[str] = Field(default_factory=list, alias="ALLOWED_PROJECTS")
    database_url: str = Field(default="", min_length=1, alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    project_context_sizes: dict[str, int] = Field(default_factory=dict, alias="PROJECT_CONTEXT_SIZES")
    fanpage_lazy_web_search: bool = Field(default=True, alias="FANPAGE_LAZY_WEB_SEARCH")
    fanpage_max_history_messages: int = Field(default=10, ge=1, alias="FANPAGE_MAX_HISTORY_MESSAGES")
    fanpage_knowledge_max_chunks: int = Field(default=3, ge=0, le=10, alias="FANPAGE_KNOWLEDGE_MAX_CHUNKS")
    fanpage_enable_failure_risk_scoring: bool = Field(default=True, alias="FANPAGE_ENABLE_FAILURE_RISK_SCORING")
    # BRANE query router: mapping query intent type -> list of diacritic-stripped regex patterns
    query_type_patterns: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "greeting": [r"\b(chao|hello|hi|ok|cam on|thanks)\b"],
            "casual_chat": [r"\b(khach hoi|tra loi ngan|mot cau|1 cau|duoi 3 cau)\b"],
            "factual_qa": [r"\b(gia|price|ti gia|rate|vang|gold|btc|bitcoin|crypto|chung khoan|stock)\b", r"\b(ai|la ai|who|chu tich|tong bi thu|thu tuong|lanh dao)\b"],
            "reasoning": [r"\b(phan tich|analysis|chien luoc|strategy|roadmap|ke hoach|plan|thiet ke|design)\b"],
            "coding": [r"\b(code|python|sql|javascript|typescript|fastapi|debug|error|traceback|bug|exception|algorithm)\b"],
            "search": [r"\b(search|tim|tra|google|web|mang|internet)\b"],
            "rag_query": [r"\b(tai lieu|document|docs|knowledge|rag|noi bo|internal|chinh sach|policy|du lieu|database|bao cao|report|quy trinh|procedure)\b"],
            "creative": [r"\b(viet|viet bai|viet thơ|thơ|van|ke chuyen|story|poem|essay)\b"],
        },
        alias="QUERY_TYPE_PATTERNS",
    )
    # BRANE query router: mapping query intent type -> preferred model override
    # Values: "lite", "normal", "external", "fast_background"
    query_type_model_map: dict[str, str] = Field(
        default_factory=lambda: {
            "greeting": "fast_background",
            "casual_chat": "fast_background",
            "coding": "normal",
            "reasoning": "normal",
        },
        alias="QUERY_TYPE_MODEL_MAP",
    )
    # Generic per-project override dict (replaces hard-coded fanpage checks)
    # Format: {"project_name": {"max_history_messages": 10, "model_mode": "lite", ...}}
    per_project_overrides: dict[str, dict] = Field(default_factory=dict, alias="PER_PROJECT_OVERRIDES")

    @field_validator("allowed_origins", "allowed_hosts", "trusted_proxy_ips", "allowed_projects", "openrouter_allowed_projects", "openrouter_denied_projects", "llama_cpp_nodes", "minimax_allowed_projects", "minimax_denied_projects", mode="before")
    @classmethod
    def _parse_string_list(cls, value: list[str] | str) -> list[str]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            parsed = json.loads(stripped)
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                return parsed
        raise ValueError("value must be a list of strings")

    @field_validator("project_context_sizes", "query_type_patterns", "query_type_model_map", "per_project_overrides", mode="before")
    @classmethod
    def _parse_dict(cls, value: dict | str) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return {}

    @field_validator("project_context_sizes")
    @classmethod
    def _validate_project_context_sizes(cls, value: dict[str, int]) -> dict[str, int]:
        for project, ctx in value.items():
            if not isinstance(ctx, int) or ctx < 512:
                raise ValueError(
                    f"project_context_sizes['{project}'] must be >= 512, got {ctx!r}"
                )
        return value

    @model_validator(mode="after")
    def _validate_failure_risk_thresholds(self) -> "Settings":
        medium = self.failure_risk_medium_threshold
        high = self.failure_risk_high_threshold
        if medium < 0 or high > 1.0 or medium >= high:
            raise ValueError(
                f"failure_risk thresholds invalid: medium={medium} (>=0), "
                f"high={high} (<=1.0), and medium < high required"
            )
        return self

    @model_validator(mode="after")
    def _validate_adaptive_max_tokens(self) -> "Settings":
        severe = self.adaptive_max_tokens_severe_pct
        cutoff = self.adaptive_max_tokens_cutoff_pct
        if severe >= cutoff:
            raise ValueError(
                f"adaptive_max_tokens invalid: severe={severe} must be < cutoff={cutoff}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
