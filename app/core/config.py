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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_openai_url: str = Field(default="http://localhost:11434/v1", alias="OLLAMA_OPENAI_URL")

    default_model: str = Field(
        default="VladimirGav/gemma4-26b-16GB-VRAM:latest",
        alias="DEFAULT_MODEL",
    )
    lite_model: str = Field(
        default="gemma4:e4b",
        alias="LITE_MODEL",
    )
    lite_num_ctx: int = Field(default=32768, alias="LITE_NUM_CTX")
    default_num_ctx: int = Field(default=8192, alias="DEFAULT_NUM_CTX")
    lite_max_history_messages: int = Field(default=60, alias="LITE_MAX_HISTORY_MESSAGES")
    request_timeout_seconds: float = Field(default=60.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_history_messages: int = Field(default=20, alias="MAX_HISTORY_MESSAGES")
    gpu_concurrency: int = Field(default=1, ge=1, alias="GPU_CONCURRENCY")
    api_key: str = Field(alias="API_KEY")
    rate_limit_per_minute: int = Field(default=5, ge=1, alias="RATE_LIMIT_PER_MINUTE")
    security_log_file: str = Field(default="security.log", alias="SECURITY_LOG_FILE")
    summary_threshold: int = Field(default=20, ge=1, alias="SUMMARY_THRESHOLD")
    summary_model: str = Field(default="gemma4:e4b", alias="SUMMARY_MODEL")
    enable_structmem: bool = Field(default=False, alias="ENABLE_STRUCTMEM")
    structmem_extraction_threshold: int = Field(default=8, ge=1, alias="STRUCTMEM_EXTRACTION_THRESHOLD")
    structmem_extraction_model: str = Field(default="gemma4:e4b", alias="STRUCTMEM_EXTRACTION_MODEL")
    structmem_consolidation_model: str = Field(default="gemma4:e4b", alias="STRUCTMEM_CONSOLIDATION_MODEL")
    structmem_max_episodic: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_EPISODIC")
    structmem_max_semantic: int = Field(default=6, ge=0, alias="STRUCTMEM_MAX_SEMANTIC")
    structmem_max_relational: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_RELATIONAL")
    structmem_max_procedural: int = Field(default=4, ge=0, alias="STRUCTMEM_MAX_PROCEDURAL")
    structmem_max_consolidated: int = Field(default=3, ge=0, alias="STRUCTMEM_MAX_CONSOLIDATED")
    structmem_debug_include_trace: bool = Field(default=False, alias="STRUCTMEM_DEBUG_INCLUDE_TRACE")
    enable_web_search_tool: bool = Field(default=True, alias="ENABLE_WEB_SEARCH_TOOL")
    web_search_max_results: int = Field(default=5, ge=1, le=10, alias="WEB_SEARCH_MAX_RESULTS")
    web_search_timeout_seconds: float = Field(default=8.0, gt=0, alias="WEB_SEARCH_TIMEOUT_SECONDS")
    enable_crew_agents: bool = Field(default=False, alias="ENABLE_CREW_AGENTS")
    crew_model: str = Field(default="gemma4:e4b", alias="CREW_MODEL")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    google_search_cx: str = Field(default="", alias="GOOGLE_SEARCH_CX")
    allowed_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_ORIGINS),
        alias="ALLOWED_ORIGINS",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, value: list[str] | str) -> list[str]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                return parsed
        raise ValueError("ALLOWED_ORIGINS must be a list of origin strings")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
