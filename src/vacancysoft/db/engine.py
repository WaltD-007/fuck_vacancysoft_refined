from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vacancysoft.settings import get_settings


def build_engine():
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        return create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False})
    return create_engine(settings.database_url, future=True)


SessionLocal = sessionmaker(bind=build_engine(), autoflush=False, autocommit=False, future=True)
