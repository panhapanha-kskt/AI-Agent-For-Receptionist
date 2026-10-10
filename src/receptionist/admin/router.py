"""Staff-only endpoints: review queue, knowledge, messages, skills, learning and speed.
All require X-Admin-Key."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from receptionist.admin.export import ExportError, export_to_faq
from receptionist.admin.schemas import (
    ApproveIn,
    ExportOut,
    FeedbackOut,
    KnowledgeOut,
    KnowledgeUpdateIn,
    MessageOut,
    QuestionOut,
    RoutingExampleOut,
    SkillsOut,
)
from receptionist.database import get_db
from receptionist.knowledge import service as knowledge
from receptionist.knowledge.models import KnowledgeEntry
from receptionist.learning import service as learning
from receptionist.learning.models import UnansweredQuestion
from receptionist.messages import service as messages
from receptionist.metrics import service as metrics
from receptionist.security import audit
from receptionist.security.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _not_found():
    return HTTPException(status_code=404, detail="Not found")


# --- Review queue (the learning loop) ---


@router.get("/questions", response_model=list[QuestionOut])
def pending_questions(db: Session = Depends(get_db)):
    return learning.list_pending(db)


@router.post("/questions/{item_id}/approve", response_model=QuestionOut)
def approve_question(
    item_id: int, body: ApproveIn, request: Request, db: Session = Depends(get_db)
):
    if request.app.state.skills.get(body.skill) is None:
        raise HTTPException(status_code=422, detail=f"Unknown skill {body.skill!r}")
    item = learning.approve(db, item_id, body.answer, body.skill, body.question)
    if item is None:
        raise _not_found()
    audit.record(db, "admin", "approve_question", {"id": item_id, "skill": body.skill})
    return item


@router.post("/questions/{item_id}/dismiss", response_model=QuestionOut)
def dismiss_question(item_id: int, db: Session = Depends(get_db)):
    item = learning.dismiss(db, item_id)
    if item is None:
        raise _not_found()
    audit.record(db, "admin", "dismiss_question", {"id": item_id})
    return item


@router.post("/questions/{item_id}/draft", response_model=QuestionOut)
async def draft_question(item_id: int, request: Request, db: Session = Depends(get_db)):
    """Ask the AI to draft an answer from the skills, for staff to edit before approving."""
    item = db.get(UnansweredQuestion, item_id)
    if item is None or item.status != "pending":
        raise _not_found()
    try:
        draft = await request.app.state.agent.draft_answer(db, item.question)
    except Exception:
        logger.exception("draft failed")
        raise HTTPException(status_code=503, detail="AI is busy, please try again.") from None
    item = learning.save_draft(
        db, item_id, draft or "NOT FOUND IN SKILLS - please write the answer."
    )
    audit.record(db, "admin", "draft_question", {"id": item_id})
    return item


# --- Knowledge ---


@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(db: Session = Depends(get_db)):
    return knowledge.list_active(db)


@router.put("/knowledge/{entry_id}", response_model=KnowledgeOut)
def update_knowledge(entry_id: int, body: KnowledgeUpdateIn, db: Session = Depends(get_db)):
    entry = knowledge.update_entry(db, entry_id, body.answer)
    if entry is None:
        raise _not_found()
    audit.record(db, "admin", "update_knowledge", {"id": entry_id, "new_id": entry.id})
    return entry


@router.delete("/knowledge/{entry_id}", status_code=204)
def delete_knowledge(entry_id: int, db: Session = Depends(get_db)):
    if not knowledge.deactivate(db, entry_id):
        raise _not_found()
    audit.record(db, "admin", "deactivate_knowledge", {"id": entry_id})


@router.post("/knowledge/{entry_id}/export", response_model=ExportOut)
def export_knowledge(entry_id: int, request: Request, db: Session = Depends(get_db)):
    """Move an approved answer into its skill's faq.md, then retire the database copy."""
    entry = db.get(KnowledgeEntry, entry_id)
    if entry is None or not entry.active:
        raise _not_found()
    registry = request.app.state.skills
    try:
        file = export_to_faq(registry, entry)
    except ExportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    knowledge.deactivate(db, entry_id)
    registry.reload()  # the router rebuilds its index on the next question
    audit.record(db, "admin", "export_knowledge", {"id": entry_id, "file": file})
    return ExportOut(skill=entry.skill, file=file)


# --- Messages for staff ---


@router.get("/messages", response_model=list[MessageOut])
def list_messages(status: str | None = None, db: Session = Depends(get_db)):
    return messages.list_messages(db, status)


@router.post("/messages/{message_id}/done", response_model=MessageOut)
def message_done(message_id: int, db: Session = Depends(get_db)):
    msg = messages.mark_done(db, message_id)
    if msg is None:
        raise _not_found()
    audit.record(db, "admin", "message_done", {"id": message_id})
    return msg


# --- Skills ---


@router.get("/skills", response_model=SkillsOut)
def list_skills(request: Request):
    registry = request.app.state.skills
    return SkillsOut(skills=registry.names(), errors=registry.errors)


@router.post("/skills/reload", response_model=SkillsOut)
def reload_skills(request: Request, db: Session = Depends(get_db)):
    registry = request.app.state.skills
    registry.reload()
    audit.record(db, "admin", "reload_skills", {"skills": registry.names()})
    return SkillsOut(skills=registry.names(), errors=registry.errors)


# --- Learning: caller feedback and learned routing examples ---


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(rating: str | None = None, db: Session = Depends(get_db)):
    return learning.list_feedback(db, rating if rating in ("up", "down") else None)


@router.get("/routing-examples", response_model=list[RoutingExampleOut])
def list_routing_examples(db: Session = Depends(get_db)):
    return learning.list_routing_examples(db)


@router.delete("/routing-examples/{example_id}", status_code=204)
def delete_routing_example(example_id: int, request: Request, db: Session = Depends(get_db)):
    if not learning.delete_routing_example(db, example_id):
        raise _not_found()
    router_ = request.app.state.agent.router
    if router_ is not None:
        router_.examples_changed()
    audit.record(db, "admin", "delete_routing_example", {"id": example_id})


# --- Speed metrics ---


@router.get("/metrics")
def turn_metrics(hours: int = 24, db: Session = Depends(get_db)):
    """p50/p95 latency, model calls per answer, cache hit ratio, slowest turns, feedback."""
    out = metrics.summary(db, hours=max(1, min(hours, 24 * 30)))
    out["feedback"] = learning.feedback_counts(db)
    return out
