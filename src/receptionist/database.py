"""Database engine, session factory and declarative base."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from receptionist.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns(bind: Engine) -> None:
    """Add new nullable/defaulted columns to tables that already exist.

    create_all() only creates missing tables, so a database made by an older version of
    the app would lack new columns. This covers simple additive changes; use Alembic for
    anything more (see need-improve.md #13).
    """
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                col_type = column.type.compile(dialect=bind.dialect)
                default = column.default.arg if column.default is not None else None
                clause = ""
                if isinstance(default, bool):
                    clause = f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    clause = f" DEFAULT {default}"
                elif isinstance(default, str):
                    clause = " DEFAULT '" + default.replace("'", "''") + "'"
                # Identifiers come from our own model definitions, never from user input.
                conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}{clause}'
                    )  # nosec B608
                )
                logger.info("added column %s.%s", table.name, column.name)


def init_db(bind: Engine | None = None) -> None:
    # Import models so they register on Base.metadata.
    from receptionist.knowledge import models as _k  # noqa: F401
    from receptionist.learning import models as _l  # noqa: F401
    from receptionist.messages import models as _m  # noqa: F401
    from receptionist.metrics import models as _mt  # noqa: F401
    from receptionist.security import audit as _a  # noqa: F401

    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    _add_missing_columns(bind)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
