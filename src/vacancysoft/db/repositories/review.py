from __future__ import annotations

from sqlalchemy.orm import Session


class ReviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
