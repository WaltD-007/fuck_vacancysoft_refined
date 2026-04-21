"""Model-level tests for the User ORM row.

Mirrors the shape of tests/test_outreach_models.py — in-memory SQLite
+ Base.metadata.create_all. Covers defaults, unique constraints, and
the ORM update path (for last_seen_at + preferences).
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from vacancysoft.db.models import Base, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


class TestUserModel:

    def test_round_trip(self, session) -> None:
        u = User(email="me@firm.com", display_name="Me")
        session.add(u)
        session.commit()
        loaded = session.execute(
            select(User).where(User.id == u.id)
        ).scalar_one()
        assert loaded.email == "me@firm.com"
        assert loaded.display_name == "Me"

    def test_defaults(self, session) -> None:
        """Required defaults: role='operator', active=True, preferences={}."""
        u = User(email="a@b.com", display_name="A")
        session.add(u)
        session.commit()
        assert u.role == "operator"
        assert u.active is True
        assert u.preferences == {}
        assert u.entra_object_id is None
        assert u.last_seen_at is None
        assert isinstance(u.created_at, datetime)
        assert isinstance(u.updated_at, datetime)

    def test_email_unique(self, session) -> None:
        session.add(User(email="dup@firm.com", display_name="First"))
        session.commit()
        session.add(User(email="dup@firm.com", display_name="Second"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_entra_object_id_unique_when_set(self, session) -> None:
        """Duplicate entra_object_id should fail, but multiple NULLs OK."""
        session.add(User(email="a@b.com", display_name="A", entra_object_id=None))
        session.add(User(email="c@d.com", display_name="C", entra_object_id=None))
        session.commit()  # two NULLs — fine
        session.add(User(email="e@f.com", display_name="E", entra_object_id="obj-1"))
        session.commit()
        session.add(User(email="g@h.com", display_name="G", entra_object_id="obj-1"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_updated_at_advances_on_modify(self, session) -> None:
        u = User(email="me@firm.com", display_name="Me")
        session.add(u)
        session.commit()
        orig = u.updated_at

        time.sleep(0.02)  # SQLite datetime resolution is ms; 20ms is safe
        u.display_name = "Me Renamed"
        session.commit()
        assert u.updated_at > orig

    def test_preferences_json_round_trip(self, session) -> None:
        u = User(email="me@firm.com", display_name="Me")
        u.preferences = {
            "dashboard_feed": {
                "category": "risk",
                "country": "UK",
                "sub_specialism": "Credit Risk",
                "employment_type": "Permanent",
            },
            "leads_page": {"sort_by": "score"},
        }
        session.add(u)
        session.commit()

        session.expire_all()  # force a round-trip through the DB
        loaded = session.execute(
            select(User).where(User.email == "me@firm.com")
        ).scalar_one()
        assert loaded.preferences["dashboard_feed"]["category"] == "risk"
        assert loaded.preferences["leads_page"]["sort_by"] == "score"

    def test_inactive_flag(self, session) -> None:
        u = User(email="me@firm.com", display_name="Me", active=False)
        session.add(u)
        session.commit()
        loaded = session.execute(select(User)).scalar_one()
        assert loaded.active is False
