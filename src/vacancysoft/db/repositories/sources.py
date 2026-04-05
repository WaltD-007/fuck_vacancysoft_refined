from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vacancysoft.db.models import Source


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active(self) -> list[Source]:
        stmt = select(Source).where(Source.active == 1)
        return list(self.session.execute(stmt).scalars())
