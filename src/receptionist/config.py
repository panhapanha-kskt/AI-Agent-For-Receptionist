"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (Google Gemini)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"
    # true = the key is a Google Cloud Vertex AI (express mode) key, not an AI Studio key
    gemini_vertexai: bool = False
    # Used when the main model is overloaded (429/5xx) or times out. Empty = no fallback.
    gemini_fallback_model: str = "gemini-3.6-flash"
    # Fast model for questions whose answer is already attached (routed skill or approved
    # answer). If it fails, the main model takes over. Empty = always use the main model.
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    # "low" is recommended for fact look-ups (fast); "medium"/"high" think longer. Empty = default.
    thinking_level: str = "low"
    llm_max_output_tokens: int = 4000
    llm_timeout_ms: int = Field(default=20000, ge=1000)
    # Total attempts per model (1 = no retry). Retries use exponential backoff with jitter.
    llm_retry_attempts: int = Field(default=2, ge=1, le=5)
    agent_max_tool_rounds: int = 6

    # Skill router: pick the skill before calling the LLM (1 call instead of 3).
    router_enabled: bool = True
    # Semantic matching with Gemini embeddings; false = keyword matching only (no API cost).
    router_embeddings: bool = True
    embedding_model: str = "gemini-embedding-2"
    # Calibrated on real questions (2026-10-10): route when the best skill scores >= 0.65
    # AND leads the runner-up by >= 0.03.
    router_threshold: float = Field(default=0.65, ge=0, le=1)
    router_margin: float = Field(default=0.03, ge=0, le=1)
    router_lexical_threshold: float = Field(default=0.35, ge=0, le=1)
    # Max size of a skill's content added to the request (SKILL.md + files that fit).
    router_bundle_max_bytes: int = Field(default=8000, ge=500)
    # Staff-approved answers are checked before calling the LLM.
    knowledge_threshold: float = Field(default=0.85, ge=0, le=1)  # embedding similarity
    knowledge_lexical_threshold: float = Field(default=0.6, ge=0, le=1)  # keyword overlap

    # App
    school_name: str = "Example Academy"
    environment: str = "development"
    database_url: str = "sqlite:///./receptionist.db"
    skills_dir: Path = Path("./skills")
    web_dir: Path = Path("./web")
    cors_origins: str = "http://localhost:8000"

    # Admin
    admin_api_key: SecretStr = SecretStr("")

    # Limits
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_audio_seconds: int = Field(default=120, gt=0)
    max_text_chars: int = Field(default=1000, gt=0)
    # Hard limit: after this many messages a conversation starts fresh (safety net).
    max_history_turns: int = 40
    # After this many caller messages, older turns are summarised in the background so long
    # chats stay fast and keep their context. 0 = never summarise.
    summarize_after_turns: int = Field(default=10, ge=0)
    rate_limit: str = "20/minute"

    # Speech-to-text
    stt_enabled: bool = False
    stt_model_en: str = "small"
    stt_model_km: str = ""
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"

    # Text-to-speech: "browser" | "mms"
    tts_provider: str = "browser"
    tts_mms_revision: str = "main"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
