"""The human-approved learning loop.

agent  --log_unanswered-->  review queue  --admin approves-->  knowledge entry
caller --👎 feedback------>  review queue (with the answer they didn't like)
router miss + model found the skill  -->  routing example (automatic, low risk)

Callers can never write knowledge directly, which prevents knowledge poisoning.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from receptionist.database import utcnow
from receptionist.knowledge import service as knowledge
from receptionist.learning.models import (
    APPROVED,
    DISMISSED,
    PENDING,
    SOURCE_AGENT,
    SOURCE_FEEDBACK,
    Feedback,
    RoutingExample,
    UnansweredQuestion,
)
from receptionist.security.redact import redact

DUPLICATE_THRESHOLD = 0.6
MAX_EXAMPLES_PER_SKILL = 50
EXAMPLE_DUPLICATE_THRESHOLD = 0.8


def log_unanswered(
    db: Session,
    question: str,
    lang: str,
    suggested_skill: str | None = None,
    source: str = SOURCE_AGENT,
    previous_answer: str | None = None,
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
            if previous_answer and not item.previous_answer:
                item.previous_answer = redact(previous_answer)[:2000]
            db.commit()
            return item
    item = UnansweredQuestion(
        question=question,
        lang=lang,
        suggested_skill=suggested_skill,
        source=source,
        previous_answer=redact(previous_answer)[:2000] if previous_answer else None,
    )
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


def save_draft(db: Session, item_id: int, draft: str) -> UnansweredQuestion | None:
    item = db.get(UnansweredQuestion, item_id)
    if item is None or item.status != PENDING:
        return None
    item.draft_answer = draft[:4000]
    db.commit()
    return item


# --- Caller feedback ---


def record_feedback(
    db: Session,
    turn_id: int,
    rating: str,
    question: str,
    answer: str,
    skill: str | None,
    lang: str,
    comment: str | None = None,
) -> Feedback | None:
    """Store a 👍/👎 (once per turn). A 👎 also puts the question in the review queue."""
    fb = Feedback(
        turn_id=turn_id,
        rating=rating,
        skill=skill,
        question=redact(question)[:1000],
        answer=redact(answer)[:2000],
        comment=redact(comment)[:500] if comment else None,
    )
    db.add(fb)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None  # already rated
    if rating == "down":
        log_unanswered(
            db,
            question,
            lang,
            suggested_skill=skill,
            source=SOURCE_FEEDBACK,
            previous_answer=answer,
        )
    return fb


def list_feedback(db: Session, rating: str | None = None, limit: int = 100) -> list[Feedback]:
    stmt = select(Feedback).order_by(Feedback.id.desc()).limit(limit)
    if rating:
        stmt = stmt.where(Feedback.rating == rating)
    return list(db.scalars(stmt))


def feedback_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Feedback.rating, func.count()).group_by(Feedback.rating)).all()
    return {rating: count for rating, count in rows}


# --- Learned routing examples ---


def add_routing_example(db: Session, skill: str, question: str) -> RoutingExample | None:
    """Remember how callers ask about `skill`. Skips near-duplicates; capped per skill."""
    text = redact(question.strip())[:300]
    if len(text) < 4:
        return None
    existing = list(db.scalars(select(RoutingExample).where(RoutingExample.skill == skill)))
    if any(knowledge.similarity(e.text, text) >= EXAMPLE_DUPLICATE_THRESHOLD for e in existing):
        return None
    if len(existing) >= MAX_EXAMPLES_PER_SKILL:
        db.delete(min(existing, key=lambda e: e.id))  # keep the newest
    example = RoutingExample(skill=skill, text=text)
    db.add(example)
    db.commit()
    return example


def routing_examples(db: Session) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in db.scalars(select(RoutingExample).order_by(RoutingExample.id)):
        out.setdefault(e.skill, []).append(e.text)
    return out


def list_routing_examples(db: Session) -> list[RoutingExample]:
    return list(
        db.scalars(select(RoutingExample).order_by(RoutingExample.skill, RoutingExample.id))
    )


def delete_routing_example(db: Session, example_id: int) -> bool:
    example = db.get(RoutingExample, example_id)
    if example is None:
        return False
    db.delete(example)
    db.commit()
    return True
