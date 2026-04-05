from __future__ import annotations

from sqlalchemy.orm import Session


class ExportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
