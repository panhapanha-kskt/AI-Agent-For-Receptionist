"""The human-approved learning loop.

agent  --log_unanswered-->  review queue  --admin approves-->  knowledge entry
Callers can never write knowledge directly, which prevents knowledge poisoning.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from receptionist.database import utcnow
from receptionist.knowledge import service as knowledge
from receptionist.learning.models import (
    APPROVED,
    DISMISSED,
    PENDING,
    UnansweredQuestion,
)
from receptionist.security.redact import redact

DUPLICATE_THRESHOLD = 0.6


def log_unanswered(
    db: Session, question: str, lang: str, suggested_skill: str | None = None
) -> UnansweredQuestion:
    question = redact(question.strip())[:1000]
    # Group repeats of the same question so staff see what is asked most.
    for item in list_pending(db):
        if (
            item.lang == lang
            and knowledge.similarity(item.question, question) >= DUPLICATE_THRESHOLD
        ):
            item.times_asked += 1
            item.last_asked_at = utcnow()
            db.commit()
            return item
    item = UnansweredQuestion(question=question, lang=lang, suggested_skill=suggested_skill)
    db.add(item)
    db.commit()
    return item


def list_pending(db: Session) -> list[UnansweredQuestion]:
    stmt = (
        select(UnansweredQuestion)
        .where(UnansweredQuestion.status == PENDING)
        .order_by(UnansweredQuestion.times_asked.desc(), UnansweredQuestion.id)
    )
    return list(db.scalars(stmt))


def approve(
    db: Session, item_id: int, answer: str, skill: str, question: str | None = None
) -> UnansweredQuestion | None:
    item = db.get(UnansweredQuestion, item_id)
    if item is None or item.status != PENDING:
        return None
    entry = knowledge.create_entry(
        db, skill=skill, question=question or item.question, answer=answer, lang=item.lang
    )
    item.status = APPROVED
    item.knowledge_entry_id = entry.id
    db.commit()
    return item


def dismiss(db: Session, item_id: int) -> UnansweredQuestion | None:
    item = db.get(UnansweredQuestion, item_id)
    if item is None or item.status != PENDING:
        return None
    item.status = DISMISSED
    db.commit()
    return item
