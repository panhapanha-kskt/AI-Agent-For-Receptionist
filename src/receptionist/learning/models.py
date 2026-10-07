"""Questions the agent could not answer, waiting for staff review."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from receptionist.database import Base, utcnow

PENDING = "pending"
APPROVED = "approved"
DISMISSED = "dismissed"


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    question: Mapped[str] = mapped_column(Text)  # stored redacted
    lang: Mapped[str] = mapped_column(String(8), default="en")
    suggested_skill: Mapped[str | None] = mapped_column(String(64), nullable=True)
    times_asked: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, index=True)
    knowledge_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id"), nullable=True
    )
