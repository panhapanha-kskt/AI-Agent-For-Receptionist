from sqlalchemy import select
from sqlalchemy.orm import Session

from receptionist.messages.models import StaffMessage


def create(
    db: Session, caller_name: str, contact: str, for_office: str, body: str, session_id: str | None
) -> StaffMessage:
    msg = StaffMessage(
        caller_name=caller_name.strip()[:120],
        contact=contact.strip()[:120],
        for_office=for_office,
        body=body.strip()[:2000],
        session_id=session_id,
    )
    db.add(msg)
    db.commit()
    return msg


def list_messages(db: Session, status: str | None = None) -> list[StaffMessage]:
    stmt = select(StaffMessage).order_by(StaffMessage.id.desc())
    if status:
        stmt = stmt.where(StaffMessage.status == status)
    return list(db.scalars(stmt))


def mark_done(db: Session, message_id: int) -> StaffMessage | None:
    msg = db.get(StaffMessage, message_id)
    if msg is None:
        return None
    msg.status = "done"
    db.commit()
    return msg
