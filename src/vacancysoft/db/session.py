from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from vacancysoft.db.engine import SessionLocal


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
