from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vacancysoft.settings import get_settings


def _ensure_sqlite_parent_exists(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    db_path = database_url.removeprefix(prefix)
    if db_path == ":memory:":
        return
    path = Path(db_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def build_engine():
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        _ensure_sqlite_parent_exists(settings.database_url)
        return create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False})
    return create_engine(settings.database_url, future=True)


SessionLocal = sessionmaker(bind=build_engine(), autoflush=False, autocommit=False, future=True)
