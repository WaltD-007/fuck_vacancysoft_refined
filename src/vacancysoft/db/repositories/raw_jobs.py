from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import RawJob


class RawJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_recent(self, limit: int = 50) -> list[RawJob]:
        stmt = select(RawJob).limit(limit)
        return list(self.session.execute(stmt).scalars())
