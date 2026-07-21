"""Backward-compatible access to the unified runtime configuration."""

from core_engine.runtime_config import get_runtime_config

runtime_config = get_runtime_config()
OPENAI_API_KEY = runtime_config.llm_api_key
OPENAI_BASE_URL = runtime_config.openai_base_url
LLM_MODEL = runtime_config.llm_model
EMBEDDING_MODEL = runtime_config.embedding_model
