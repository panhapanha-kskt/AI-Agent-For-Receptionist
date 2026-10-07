"""Staff-only endpoints: review queue, knowledge, messages, skills. All require X-Admin-Key."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from receptionist.admin.schemas import (
    ApproveIn,
    KnowledgeOut,
    KnowledgeUpdateIn,
    MessageOut,
    QuestionOut,
    SkillsOut,
)
from receptionist.database import get_db
from receptionist.knowledge import service as knowledge
from receptionist.learning import service as learning
from receptionist.messages import service as messages
from receptionist.security import audit
from receptionist.security.auth import require_admin

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
