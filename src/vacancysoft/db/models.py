from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    employer_name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(Text)
    adapter_name: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(50))
    active: Mapped[int] = mapped_column(Integer, default=1)


class RawJob(Base):
    __tablename__ = "raw_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    external_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discovered_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    completeness_score: Mapped[float] = mapped_column(Float)
    extraction_confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String(32), default=lambda: datetime.utcnow().isoformat())
