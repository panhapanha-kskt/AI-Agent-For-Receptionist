"""One row per conversation turn: where the time went and how many tokens were used.

No caller text is stored here, only numbers and labels, so this table holds no personal data.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from receptionist.database import Base, utcnow


class TurnMetric(Base):
    __tablename__ = "turn_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="text")  # text | voice | stream
    # How the answer was produced: "tools" (model chose skills itself), "routed" (server
    # picked the skill), "knowledge" (approved answer matched).
    path: Mapped[str] = mapped_column(String(16), default="tools")
    skill: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="answered")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    # Milliseconds
    route_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # time to first text
    llm_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_ms: Mapped[int] = mapped_column(Integer, default=0)
    stt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tts_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Tokens (from Gemini usage_metadata)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thought_tokens: Mapped[int] = mapped_column(Integer, default=0)
