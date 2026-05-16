"""Typed application settings loaded from environment (.env supported)."""

from functools import lru_cache
import json

from pydantic import Field, field_validator
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

    default_model: str = Field(
        default="local-gemma4-e4b-q8",
        alias="DEFAULT_MODEL",
    )
    lite_model: str = Field(
        default="local-gemma4-e4b-q4",
        alias="LITE_MODEL",
    )
    lite_num_ctx: int = Field(default=65536, alias="LITE_NUM_CTX")
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
    ai_max_tokens: int = Field(default=0, ge=0, alias="AI_MAX_TOKENS")
    local_max_tokens: int = Field(default=0, ge=0, alias="LOCAL_MAX_TOKENS")
    openrouter_max_tokens: int = Field(default=0, ge=0, alias="OPENROUTER_MAX_TOKENS")
    ai_top_p: float = Field(default=0.0, ge=0.0, le=1.0, alias="AI_TOP_P")
    provider_call_timeout_seconds: float = Field(default=0.0, ge=0.0, alias="PROVIDER_CALL_TIMEOUT_SECONDS")
    api_key: str = Field(alias="API_KEY")
    rate_limit_per_minute: int = Field(default=5, ge=1, alias="RATE_LIMIT_PER_MINUTE")
    security_log_file: str = Field(default="security.log", alias="SECURITY_LOG_FILE")
    public_health_enabled: bool = Field(default=True, alias="PUBLIC_HEALTH_ENABLED")
    public_docs_enabled: bool = Field(default=True, alias="PUBLIC_DOCS_ENABLED")
    auth_failure_limit: int = Field(default=10, ge=1, alias="AUTH_FAILURE_LIMIT")
    auth_failure_block_seconds: int = Field(default=900, ge=1, alias="AUTH_FAILURE_BLOCK_SECONDS")
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
    enable_web_search_tool: bool = Field(default=True, alias="ENABLE_WEB_SEARCH_TOOL")
    enable_failure_risk: bool = Field(default=True, alias="ENABLE_FAILURE_RISK")
    failure_risk_log_only: bool = Field(default=True, alias="FAILURE_RISK_LOG_ONLY")
    failure_risk_enable_actions: bool = Field(default=False, alias="FAILURE_RISK_ENABLE_ACTIONS")
    failure_risk_high_threshold: float = Field(default=0.6, ge=0.0, le=1.0, alias="FAILURE_RISK_HIGH_THRESHOLD")
    failure_risk_medium_threshold: float = Field(default=0.3, ge=0.0, le=1.0, alias="FAILURE_RISK_MEDIUM_THRESHOLD")
    failure_risk_enable_search_action: bool = Field(default=True, alias="FAILURE_RISK_ENABLE_SEARCH_ACTION")
    enable_knowledge_rag: bool = Field(default=True, alias="ENABLE_KNOWLEDGE_RAG")
    knowledge_max_chunks: int = Field(default=4, ge=0, le=10, alias="KNOWLEDGE_MAX_CHUNKS")
    knowledge_chunk_chars: int = Field(default=2000, ge=500, le=8000, alias="KNOWLEDGE_CHUNK_CHARS")
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
    llama_cpp_nodes: list[str] = Field(default_factory=list, alias="LLAMA_CPP_NODES")
    web_search_max_results: int = Field(default=5, ge=1, le=10, alias="WEB_SEARCH_MAX_RESULTS")
    web_search_timeout_seconds: float = Field(default=8.0, gt=0, alias="WEB_SEARCH_TIMEOUT_SECONDS")
    enable_crew_agents: bool = Field(default=False, alias="ENABLE_CREW_AGENTS")
    crew_model: str = Field(default="local-gemma4-e4b-q4", alias="CREW_MODEL")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    google_search_cx: str = Field(default="", alias="GOOGLE_SEARCH_CX")
    searxng_base_url: str = Field(default="", alias="SEARXNG_BASE_URL")
    allowed_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS),
        alias="ALLOWED_ORIGINS",
    )
    allowed_hosts: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS),
        alias="ALLOWED_HOSTS",
    )
    database_url: str = Field(default="", alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    project_context_sizes: dict[str, int] = Field(default_factory=dict, alias="PROJECT_CONTEXT_SIZES")
    fanpage_lazy_web_search: bool = Field(default=True, alias="FANPAGE_LAZY_WEB_SEARCH")
    fanpage_max_history_messages: int = Field(default=10, ge=1, alias="FANPAGE_MAX_HISTORY_MESSAGES")
    fanpage_knowledge_max_chunks: int = Field(default=3, ge=0, le=10, alias="FANPAGE_KNOWLEDGE_MAX_CHUNKS")
    fanpage_enable_failure_risk_scoring: bool = Field(default=True, alias="FANPAGE_ENABLE_FAILURE_RISK_SCORING")

    @field_validator("allowed_origins", "allowed_hosts", "openrouter_allowed_projects", "openrouter_denied_projects", "llama_cpp_nodes", mode="before")
    @classmethod
    def _parse_string_list(cls, value: list[str] | str) -> list[str]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                return parsed
        raise ValueError("value must be a list of strings")

    @field_validator("project_context_sizes", mode="before")
    @classmethod
    def _parse_dict(cls, value: dict | str) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
