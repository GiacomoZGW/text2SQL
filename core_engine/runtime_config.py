"""Typed, single-source runtime configuration for LLM and embedding clients."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class ModelRuntimeConfig(BaseModel):
    """Validated model settings shared by agents, embeddings, and workers."""

    openai_api_key: str | None = None
    dashscope_api_key: str | None = None
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=60, ge=1, le=600)
    llm_max_retries: int = Field(default=3, ge=0, le=10)
    llm_max_tokens: int = Field(default=4096, ge=128, le=32768)
    embedding_model: str = "text-embedding-v4"
    embedding_max_retries: int = Field(default=3, ge=0, le=10)

    @field_validator("openai_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("OPENAI_BASE_URL must start with http:// or https://")
        return normalized

    @field_validator("llm_model", "embedding_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model names cannot be blank")
        return normalized

    @property
    def llm_api_key(self) -> str | None:
        """DashScope keys also work with its OpenAI-compatible API endpoint."""
        return self.openai_api_key or self.dashscope_api_key

    @property
    def credential_source(self) -> str | None:
        if self.openai_api_key:
            return "OPENAI_API_KEY"
        if self.dashscope_api_key:
            return "DASHSCOPE_API_KEY"
        return None

    @property
    def llm_configured(self) -> bool:
        return self.credential_source is not None

    def require_llm_credentials(self, component: str = "runtime") -> None:
        if self.llm_configured:
            return
        raise RuntimeError(
            f"{component} requires OPENAI_API_KEY or DASHSCOPE_API_KEY. "
            "Set it in .env for local runs or .env.docker for Docker Compose."
        )

    def public_status(self) -> dict[str, str | bool]:
        """Expose readiness details without ever returning credentials."""
        return {
            "status": "ready" if self.llm_configured else "not_configured",
            "model": self.llm_model,
            "provider": "openai_compatible",
            "credential_source": self.credential_source or "missing",
        }


def _value(environ: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def load_runtime_config(environ: Mapping[str, str] | None = None) -> ModelRuntimeConfig:
    """Load config from process variables while preserving explicit environment values."""
    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        environ = os.environ
    return ModelRuntimeConfig(
        openai_api_key=_value(environ, "OPENAI_API_KEY") or None,
        dashscope_api_key=_value(environ, "DASHSCOPE_API_KEY") or None,
        openai_base_url=_value(environ, "OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        llm_model=_value(environ, "LLM_MODEL", "deepseek-v4-flash"),
        llm_temperature=_value(environ, "LLM_TEMPERATURE", "0.1"),
        llm_timeout_seconds=_value(environ, "LLM_TIMEOUT_SECONDS", "60"),
        llm_max_retries=_value(environ, "LLM_MAX_RETRIES", "3"),
        llm_max_tokens=_value(environ, "LLM_MAX_TOKENS", "4096"),
        embedding_model=_value(environ, "EMBEDDING_MODEL", "text-embedding-v4"),
        embedding_max_retries=_value(environ, "EMBEDDING_MAX_RETRIES", "3"),
    )


@lru_cache(maxsize=1)
def get_runtime_config() -> ModelRuntimeConfig:
    return load_runtime_config()
