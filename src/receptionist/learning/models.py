"""Learning data: questions for staff review, caller feedback, learned routing examples."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from receptionist.database import Base, utcnow

PENDING = "pending"
APPROVED = "approved"
DISMISSED = "dismissed"

# Where a review-queue item came from.
SOURCE_AGENT = "agent"  # the agent couldn't answer (log_unanswered)
SOURCE_FEEDBACK = "feedback"  # a caller rated an answer 👎


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
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_AGENT)
    # For 👎 feedback: the answer the caller didn't like (redacted).
    previous_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI-drafted answer for staff to edit (never shown to callers until approved).
    draft_answer: Mapped[str | None] = mapped_column(Text, nullable=True)


class Feedback(Base):
    """A caller's 👍/👎 on one answer. Question and answer are stored redacted."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    turn_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    rating: Mapped[str] = mapped_column(String(8))  # "up" | "down"
    skill: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class RoutingExample(Base):
    """A real question the router missed but the model resolved to `skill`.

    Learned automatically: it only affects WHICH approved skill content is loaded,
    never what the facts are. Staff can review and delete them on the admin page.
    """

    __tablename__ = "routing_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    skill: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
