"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Claude
    anthropic_api_key: SecretStr = SecretStr("")
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"
    claude_max_tokens: int = 4000
    agent_max_tool_rounds: int = 6

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
    max_history_turns: int = 20
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
