"""Database engine, session factory and declarative base."""

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from receptionist.config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(bind: Engine | None = None) -> None:
    # Import models so they register on Base.metadata.
    from receptionist.knowledge import models as _k  # noqa: F401
    from receptionist.learning import models as _l  # noqa: F401
    from receptionist.messages import models as _m  # noqa: F401
    from receptionist.security import audit as _a  # noqa: F401

    Base.metadata.create_all(bind=bind or engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
