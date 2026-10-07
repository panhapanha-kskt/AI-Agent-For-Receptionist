"""Approved Q&A knowledge entries. Only admins create these (via the review queue)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from receptionist.database import Base, utcnow


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    skill: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    # Editing an entry adds a new version and deactivates the old one, so history is kept.
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    replaces_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id"), nullable=True
    )
    approved_by: Mapped[str] = mapped_column(String(64), default="admin")
