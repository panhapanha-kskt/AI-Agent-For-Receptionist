"""Create, version and search approved knowledge entries."""

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from receptionist.knowledge.models import KnowledgeEntry

MIN_SCORE = 0.2
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return _PUNCT.sub(" ", text)


def _features(text: str) -> set[str]:
    """Words plus character bigrams.

    Khmer is written without spaces between words, so word matching alone fails;
    character bigrams give a language-neutral fuzzy match.
    """
    norm = _normalize(text)
    words = {w for w in norm.split() if len(w) > 1}
    compact = norm.replace(" ", "")
    bigrams = {compact[i : i + 2] for i in range(len(compact) - 1)}
    return words | bigrams


def similarity(a: str, b: str) -> float:
    fa, fb = _features(a), _features(b)
    if not fa or not fb:
        return 0.0
    return len(fa & fb) / len(fa | fb)


def search(db: Session, query: str, skill: str | None = None, limit: int = 3):
    stmt = select(KnowledgeEntry).where(KnowledgeEntry.active.is_(True))
    if skill:
        stmt = stmt.where(KnowledgeEntry.skill == skill)
    scored = [(similarity(query, e.question + " " + e.answer), e) for e in db.scalars(stmt)]
    scored = [(s, e) for s, e in scored if s >= MIN_SCORE]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


def create_entry(
    db: Session, skill: str, question: str, answer: str, lang: str, approved_by: str = "admin"
) -> KnowledgeEntry:
    entry = KnowledgeEntry(
        skill=skill, question=question, answer=answer, lang=lang, approved_by=approved_by
    )
    db.add(entry)
    db.commit()
    return entry


def update_entry(db: Session, entry_id: int, answer: str) -> KnowledgeEntry | None:
    old = db.get(KnowledgeEntry, entry_id)
    if old is None or not old.active:
        return None
    old.active = False
    new = KnowledgeEntry(
        skill=old.skill,
        question=old.question,
        answer=answer,
        lang=old.lang,
        version=old.version + 1,
        replaces_id=old.id,
    )
    db.add(new)
    db.commit()
    return new


def deactivate(db: Session, entry_id: int) -> bool:
    entry = db.get(KnowledgeEntry, entry_id)
    if entry is None:
        return False
    entry.active = False
    db.commit()
    return True


def list_active(db: Session) -> list[KnowledgeEntry]:
    stmt = (
        select(KnowledgeEntry)
        .where(KnowledgeEntry.active.is_(True))
        .order_by(KnowledgeEntry.skill, KnowledgeEntry.id)
    )
    return list(db.scalars(stmt))
