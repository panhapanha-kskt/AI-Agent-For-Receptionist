"""Audit log of agent tool calls and admin actions."""

import json
import logging
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from receptionist.database import Base, utcnow
from receptionist.security.redact import redact

logger = logging.getLogger("receptionist.audit")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(32))  # "agent" | "admin"
    action: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")


def record(db: Session, actor: str, action: str, detail: dict, session_id: str | None = None):
    # Details are redacted before storage: the audit log must not become a PII store.
    text = redact(json.dumps(detail, ensure_ascii=False, default=str))[:4000]
    db.add(AuditEvent(actor=actor, action=action, session_id=session_id, detail=text))
    db.commit()
    logger.info("%s %s session=%s", actor, action, session_id)
