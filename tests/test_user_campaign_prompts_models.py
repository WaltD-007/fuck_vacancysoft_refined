"""Model-level tests for the UserCampaignPrompt ORM row.

Pattern mirrors tests/test_users_models.py — in-memory SQLite +
Base.metadata.create_all. Covers the round-trip, default, unique
constraint and update-timestamp paths for the campaign voice layer
introduced by alembic migration 0011.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import Base, User, UserCampaignPrompt


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def user(session) -> User:
    u = User(email="me@firm.com", display_name="Me")
    session.add(u)
    session.commit()
    return u


class TestUserCampaignPromptModel:

    def test_round_trip(self, session, user) -> None:
        row = UserCampaignPrompt(
            user_id=user.id,
            tone="informal",
            instructions_text="I keep mine short and use 'cheers' sometimes.",
        )
        session.add(row)
        session.commit()
        loaded = session.execute(
            select(UserCampaignPrompt).where(UserCampaignPrompt.id == row.id)
        ).scalar_one()
        assert loaded.user_id == user.id
        assert loaded.tone == "informal"
        assert "cheers" in loaded.instructions_text

    def test_defaults(self, session, user) -> None:
        """`instructions_text` defaults to empty string — 'no override'."""
        row = UserCampaignPrompt(user_id=user.id, tone="formal")
        session.add(row)
        session.commit()
        assert row.instructions_text == ""
        # created_at / updated_at populated by the ORM defaults
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_unique_user_tone_enforced(self, session, user) -> None:
        """Two rows for the same (user, tone) must fail — six rows per user max."""
        session.add(UserCampaignPrompt(user_id=user.id, tone="formal"))
        session.commit()

        # Different tones for the same user are fine.
        session.add(UserCampaignPrompt(user_id=user.id, tone="informal"))
        session.commit()

        # Duplicate (user, tone) raises.
        session.add(UserCampaignPrompt(user_id=user.id, tone="formal"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_different_users_can_share_tone(self, session, user) -> None:
        """Two different users each setting 'formal' is fine."""
        other = User(email="b@firm.com", display_name="B")
        session.add(other)
        session.commit()

        session.add(UserCampaignPrompt(user_id=user.id, tone="formal", instructions_text="X"))
        session.add(UserCampaignPrompt(user_id=other.id, tone="formal", instructions_text="Y"))
        session.commit()   # no IntegrityError

        rows = session.execute(
            select(UserCampaignPrompt).where(UserCampaignPrompt.tone == "formal")
        ).scalars().all()
        assert len(rows) == 2

    def test_updated_at_advances_on_modify(self, session, user) -> None:
        row = UserCampaignPrompt(
            user_id=user.id, tone="informal", instructions_text="old",
        )
        session.add(row)
        session.commit()
        first = row.updated_at

        # SQLAlchemy's onupdate fires only on an actual change; sleep a
        # bit to guarantee a distinct timestamp under the default
        # datetime-now resolution.
        time.sleep(0.01)
        row.instructions_text = "new"
        session.commit()
        session.refresh(row)
        assert row.updated_at > first
