"""Messages left by callers for school staff (human handoff)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from receptionist.database import Base, utcnow

OFFICES = (
    "admissions",
    "finance",
    "academic",
    "principal",
    "front-office",
)


class StaffMessage(Base):
    __tablename__ = "staff_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    caller_name: Mapped[str] = mapped_column(String(120))
    contact: Mapped[str] = mapped_column(String(120))
    for_office: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
