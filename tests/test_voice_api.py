"""End-to-end tests for the voice-layer routes.

Matches tests/test_users_api.py — FastAPI TestClient + in-memory
SQLite + StaticPool so every SessionLocal() call shares the same DB.

Covers:

  - GET /api/users/me/campaign-prompts: cold start → all six tones empty
  - PUT with a single tone → that tone set, the other five unchanged
  - PUT with empty string on an existing tone → cleared (row kept)
  - PUT with missing key (not in payload) → that tone left alone
  - PUT with unknown tone key → 400
  - PUT with non-string value → 400
  - Multi-user isolation: user A's prompts don't leak to user B
  - GET /api/users/me/voice-samples: empty when sent_messages empty;
    returns populated samples when rows exist (seeded directly)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vacancysoft.api.routes import users as users_module
from vacancysoft.api.routes import voice as voice_module
from vacancysoft.api.server import app
from vacancysoft.db.models import (
    Base,
    CampaignOutput,
    IntelligenceDossier,
    SentMessage,
    User,
    UserCampaignPrompt,
    VoiceTrainingSample,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, session_factory):
    """Patch SessionLocal in both route modules — voice.py and
    users.py (for the identity resolver's single-user fallback path)."""
    monkeypatch.setattr(voice_module, "SessionLocal", session_factory)
    monkeypatch.setattr(users_module, "SessionLocal", session_factory)
    monkeypatch.delenv("PROSPERO_ADMIN_TOKEN", raising=False)
    return TestClient(app)


def _add_user(session_factory, email: str, *, display_name: str = "Test") -> str:
    with session_factory() as s:
        u = User(email=email.lower(), display_name=display_name, active=True)
        s.add(u)
        s.commit()
        return u.id


# ── GET /api/users/me/campaign-prompts ─────────────────────────────


class TestGetCampaignPrompts:

    def test_cold_start_returns_all_six_empty(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        resp = client.get(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "formal": "",
            "informal": "",
            "consultative": "",
            "direct": "",
            "candidate_spec": "",
            "technical": "",
        }

    def test_returns_stored_rows(self, client, session_factory) -> None:
        uid = _add_user(session_factory, "ab@firm.com")
        with session_factory() as s:
            s.add(UserCampaignPrompt(user_id=uid, tone="informal", instructions_text="X"))
            s.add(UserCampaignPrompt(user_id=uid, tone="formal", instructions_text="Y"))
            s.commit()
        resp = client.get(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["informal"] == "X"
        assert body["formal"] == "Y"
        assert body["consultative"] == ""

    def test_missing_user_401s(self, client, session_factory) -> None:
        """No users bootstrapped and no header — identity resolver 401s."""
        resp = client.get("/api/users/me/campaign-prompts")
        assert resp.status_code == 401


# ── PUT /api/users/me/campaign-prompts ─────────────────────────────


class TestPutCampaignPrompts:

    def test_upsert_single_tone(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        resp = client.put(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json={"informal": "Keep it short."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["informal"] == "Keep it short."
        # Other five untouched
        assert body["formal"] == ""
        assert body["technical"] == ""

    def test_missing_key_leaves_tone_alone(self, client, session_factory) -> None:
        """A subsequent PUT with a different tone must not clobber the first."""
        _add_user(session_factory, "ab@firm.com")
        headers = {"X-Prospero-User-Email": "ab@firm.com"}

        r1 = client.put(
            "/api/users/me/campaign-prompts",
            headers=headers,
            json={"informal": "Stays put."},
        )
        assert r1.status_code == 200

        r2 = client.put(
            "/api/users/me/campaign-prompts",
            headers=headers,
            json={"formal": "New formal."},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["informal"] == "Stays put."
        assert body["formal"] == "New formal."

    def test_empty_string_clears_tone(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        headers = {"X-Prospero-User-Email": "ab@firm.com"}
        client.put(
            "/api/users/me/campaign-prompts",
            headers=headers,
            json={"informal": "Before."},
        )
        r = client.put(
            "/api/users/me/campaign-prompts",
            headers=headers,
            json={"informal": ""},
        )
        assert r.status_code == 200
        assert r.json()["informal"] == ""

    def test_unknown_tone_key_400s(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        r = client.put(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json={"bogus_tone": "x"},
        )
        assert r.status_code == 400
        assert "unknown tone keys" in r.json()["detail"]

    def test_non_string_value_400s(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        r = client.put(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json={"formal": 42},
        )
        assert r.status_code == 400

    def test_non_dict_body_rejected(self, client, session_factory) -> None:
        """Lists / other non-dict bodies rejected. FastAPI returns 422
        via its own request-body coercion before the handler even
        runs (`payload: dict` declaration). The handler's 400 branch
        is a belt-and-braces for callers that somehow sneak past
        the type check. Either status is acceptable here."""
        _add_user(session_factory, "ab@firm.com")
        r = client.put(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json=["not", "a", "dict"],
        )
        assert r.status_code in (400, 422)

    def test_user_isolation(self, client, session_factory) -> None:
        """User A's prompts must never leak into user B's view."""
        _add_user(session_factory, "a@firm.com", display_name="A")
        _add_user(session_factory, "b@firm.com", display_name="B")

        client.put(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "a@firm.com"},
            json={"informal": "A's voice."},
        )

        # B's view should be all empty
        r = client.get(
            "/api/users/me/campaign-prompts",
            headers={"X-Prospero-User-Email": "b@firm.com"},
        )
        assert r.status_code == 200
        assert r.json()["informal"] == ""


# ── GET /api/users/me/voice-samples ────────────────────────────────


def _seed_sent_message(
    session_factory,
    *,
    sender_email: str,
    sequence: int,
    subject: str,
    body: str,
    tone: str = "informal",
    status: str = "sent",
    sent_at: datetime | None = None,
) -> None:
    """Insert a SentMessage row bypassing the (absent) send flow so
    the voice-samples endpoint has data to return. Creates the
    required CampaignOutput + IntelligenceDossier parents too since
    SentMessage.campaign_output_id is a FK.
    """
    with session_factory() as s:
        # Minimum viable parent chain to satisfy FKs.
        from uuid import uuid4
        from vacancysoft.db.models import EnrichedJob, RawJob, Source, SourceRun, ExtractionAttempt

        src = Source(
            source_key=f"seed-{uuid4()}",
            employer_name="Example",
            base_url="https://example.com",
            hostname="example.com",
            source_type="direct",
            adapter_name="greenhouse",
            active=True,
            seed_type="manual_seed",
            fingerprint=f"fp-{uuid4()}",
            canonical_company_key="example",
            capability_blob={},
        )
        s.add(src)
        s.flush()
        run = SourceRun(
            id=str(uuid4()), source_id=src.id, run_type="discovery",
            status="success", trigger="manual",
        )
        s.add(run)
        s.flush()
        attempt = ExtractionAttempt(
            id=str(uuid4()), source_run_id=run.id, source_id=src.id,
            stage="listing", method="api", success=True,
        )
        s.add(attempt)
        s.flush()
        raw = RawJob(
            source_id=src.id,
            source_run_id=run.id,
            extraction_attempt_id=attempt.id,
            external_job_id=f"ext-{uuid4()}",
            discovered_url=f"https://example.com/{uuid4()}",
            title_raw="Test",
            job_fingerprint=f"rfp-{uuid4()}",
            is_deleted_at_source=False,
        )
        s.add(raw)
        s.flush()
        ej = EnrichedJob(
            raw_job_id=raw.id,
            canonical_job_key=f"ck-{uuid4()}",
            title="Test",
            detail_fetch_status="enriched",
        )
        s.add(ej)
        s.flush()
        dossier = IntelligenceDossier(
            enriched_job_id=ej.id,
            prompt_version="test",
            category_used="risk",
            model_used="gpt-test",
            raw_response="{}",
        )
        s.add(dossier)
        s.flush()
        campaign = CampaignOutput(
            dossier_id=dossier.id,
            model_used="gpt-test",
            outreach_emails={"emails": []},
        )
        s.add(campaign)
        s.flush()

        s.add(SentMessage(
            campaign_output_id=campaign.id,
            sender_user_id=sender_email,
            recipient_email="hm@target.com",
            sequence_index=sequence,
            tone=tone,
            scheduled_for=datetime.utcnow(),
            sent_at=sent_at or datetime.utcnow(),
            subject=subject,
            body=body,
            status=status,
        ))
        s.commit()


class TestGetVoiceSamples:

    def test_cold_start_empty_per_sequence(self, client, session_factory) -> None:
        """Fresh user with no sent_messages — all five sequences are empty lists."""
        _add_user(session_factory, "ab@firm.com")
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        # JSON keys stringified; values lists.
        body = r.json()
        for seq in ("1", "2", "3", "4", "5"):
            assert body[seq] == []

    def test_returns_sent_messages_grouped_by_sequence(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=1, subject="First", body="Hi there.",
        )
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=3, subject="Mid", body="Middle body.",
        )
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["1"]) == 1
        assert body["1"][0]["subject"] == "First"
        assert len(body["3"]) == 1
        assert body["3"][0]["body"] == "Middle body."
        # Untouched sequences still empty
        assert body["2"] == []

    def test_window_caps_at_five_per_sequence(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        for i in range(7):
            _seed_sent_message(
                session_factory, sender_email="ab@firm.com",
                sequence=1, subject=f"Subj {i}", body=f"Body {i}",
                sent_at=datetime.utcnow() - timedelta(minutes=7 - i),
            )
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        assert len(r.json()["1"]) == 5

    def test_failed_and_pending_excluded(self, client, session_factory) -> None:
        """status != 'sent' rows should NOT appear in the sample pool."""
        _add_user(session_factory, "ab@firm.com")
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=1, subject="OK", body="Good.",
            status="sent",
        )
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=1, subject="FAIL", body="Bad.",
            status="failed",
        )
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=1, subject="PENDING", body="Not yet.",
            status="pending",
        )
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        samples = r.json()["1"]
        assert len(samples) == 1
        assert samples[0]["subject"] == "OK"

    def test_isolation_across_users(self, client, session_factory) -> None:
        _add_user(session_factory, "a@firm.com", display_name="A")
        _add_user(session_factory, "b@firm.com", display_name="B")
        _seed_sent_message(
            session_factory, sender_email="a@firm.com",
            sequence=1, subject="A's mail", body="A's body.",
        )
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "b@firm.com"},
        )
        assert r.status_code == 200
        # B sees none of A's sends
        assert r.json()["1"] == []


# ── POST /api/users/me/voice-training-samples ──────────────────────


class TestPostVoiceTrainingSample:
    """Operator-authored training samples, saved from the Builder's
    "Save as training sample" button. Seeds the voice-sample pool
    before the Graph send flow exists."""

    def _valid_body(self, **overrides) -> dict:
        body = {
            "sequence_index": 1,
            "tone": "informal",
            "subject": "A quick thought on the risk role",
            "body": "Hi. I work for Barclay Simpson. Cheers.",
        }
        body.update(overrides)
        return body

    def test_happy_path(self, client, session_factory) -> None:
        uid = _add_user(session_factory, "ab@firm.com")
        r = client.post(
            "/api/users/me/voice-training-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json=self._valid_body(),
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["id"]
        assert payload["created_at"]
        # DB row persisted under this user
        with session_factory() as s:
            rows = list(s.execute(
                __import__("sqlalchemy").select(VoiceTrainingSample)
                .where(VoiceTrainingSample.user_id == uid)
            ).scalars())
            assert len(rows) == 1
            assert rows[0].sequence_index == 1
            assert rows[0].tone == "informal"

    def test_unknown_tone_400(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        r = client.post(
            "/api/users/me/voice-training-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json=self._valid_body(tone="bogus"),
        )
        assert r.status_code == 400

    def test_sequence_out_of_range_400(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        for bad in (0, 6, -1, 99):
            r = client.post(
                "/api/users/me/voice-training-samples",
                headers={"X-Prospero-User-Email": "ab@firm.com"},
                json=self._valid_body(sequence_index=bad),
            )
            assert r.status_code == 400, f"sequence_index={bad} should 400"

    def test_empty_subject_or_body_400(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        for field in ("subject", "body"):
            r = client.post(
                "/api/users/me/voice-training-samples",
                headers={"X-Prospero-User-Email": "ab@firm.com"},
                json=self._valid_body(**{field: ""}),
            )
            assert r.status_code == 400

    def test_subject_over_500_chars_400(self, client, session_factory) -> None:
        _add_user(session_factory, "ab@firm.com")
        r = client.post(
            "/api/users/me/voice-training-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json=self._valid_body(subject="X" * 501),
        )
        assert r.status_code == 400

    def test_source_enriched_job_id_stored(self, client, session_factory) -> None:
        uid = _add_user(session_factory, "ab@firm.com")
        r = client.post(
            "/api/users/me/voice-training-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
            json=self._valid_body(source_enriched_job_id="ej-abc-123"),
        )
        assert r.status_code == 200
        with session_factory() as s:
            row = s.execute(
                __import__("sqlalchemy").select(VoiceTrainingSample)
                .where(VoiceTrainingSample.user_id == uid)
            ).scalar_one()
            assert row.source_enriched_job_id == "ej-abc-123"

    def test_multiple_samples_per_user_seq_allowed(self, client, session_factory) -> None:
        """Operator can iterate — every save is kept; no dedupe on
        (user, sequence, tone)."""
        uid = _add_user(session_factory, "ab@firm.com")
        for i in range(3):
            r = client.post(
                "/api/users/me/voice-training-samples",
                headers={"X-Prospero-User-Email": "ab@firm.com"},
                json=self._valid_body(subject=f"v{i}"),
            )
            assert r.status_code == 200
        with session_factory() as s:
            rows = list(s.execute(
                __import__("sqlalchemy").select(VoiceTrainingSample)
                .where(VoiceTrainingSample.user_id == uid)
            ).scalars())
            assert len(rows) == 3


# ── GET /api/users/me/voice-samples — union with training rows ─────


class TestVoiceSamplesUnion:
    """Training samples and real sends show up together in the
    voice-samples endpoint, newest first, capped at the window size."""

    def test_training_only_when_no_sends(self, client, session_factory) -> None:
        """Pre-send-flow world: operator has authored 2 training
        samples, no SentMessage rows. Both come back."""
        uid = _add_user(session_factory, "ab@firm.com")
        with session_factory() as s:
            s.add(VoiceTrainingSample(
                user_id=uid, sequence_index=1, tone="informal",
                subject="first", body="first body",
            ))
            s.add(VoiceTrainingSample(
                user_id=uid, sequence_index=1, tone="informal",
                subject="second", body="second body",
            ))
            s.commit()
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        subjects = [s["subject"] for s in r.json()["1"]]
        assert set(subjects) == {"first", "second"}

    def test_real_sends_and_training_merged_newest_first(
        self, client, session_factory
    ) -> None:
        """Real SentMessage + VoiceTrainingSample in the same sequence
        merge by timestamp, newest first, capped at 5."""
        _add_user(session_factory, "ab@firm.com")
        # Seed 2 real sends (dated)
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=2, subject="sent-old", body="old body",
            sent_at=datetime.utcnow() - timedelta(days=5),
        )
        _seed_sent_message(
            session_factory, sender_email="ab@firm.com",
            sequence=2, subject="sent-new", body="new body",
            sent_at=datetime.utcnow() - timedelta(hours=1),
        )
        # Plus a training sample created "now" (most recent)
        with session_factory() as s:
            u = s.execute(
                __import__("sqlalchemy").select(User)
                .where(User.email == "ab@firm.com")
            ).scalar_one()
            s.add(VoiceTrainingSample(
                user_id=u.id, sequence_index=2, tone="direct",
                subject="training-newest", body="training body",
            ))
            s.commit()

        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        samples = r.json()["2"]
        assert len(samples) == 3
        # Newest first: training-newest, sent-new, sent-old
        assert samples[0]["subject"] == "training-newest"
        assert samples[1]["subject"] == "sent-new"
        assert samples[2]["subject"] == "sent-old"

    def test_window_caps_merged_pool_at_five(self, client, session_factory) -> None:
        """If training + sends together exceed 5, only the newest 5
        are returned."""
        uid = _add_user(session_factory, "ab@firm.com")
        # 3 real sends, 4 training samples = 7 total
        for i in range(3):
            _seed_sent_message(
                session_factory, sender_email="ab@firm.com",
                sequence=1, subject=f"sent-{i}", body=f"body-{i}",
                sent_at=datetime.utcnow() - timedelta(hours=10 + i),
            )
        with session_factory() as s:
            for i in range(4):
                s.add(VoiceTrainingSample(
                    user_id=uid, sequence_index=1, tone="informal",
                    subject=f"training-{i}", body=f"body",
                ))
            s.commit()
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "ab@firm.com"},
        )
        assert r.status_code == 200
        assert len(r.json()["1"]) == 5

    def test_training_samples_isolated_per_user(self, client, session_factory) -> None:
        a_id = _add_user(session_factory, "a@firm.com", display_name="A")
        _add_user(session_factory, "b@firm.com", display_name="B")
        with session_factory() as s:
            s.add(VoiceTrainingSample(
                user_id=a_id, sequence_index=1, tone="informal",
                subject="A's training", body="A's body",
            ))
            s.commit()
        r = client.get(
            "/api/users/me/voice-samples",
            headers={"X-Prospero-User-Email": "b@firm.com"},
        )
        assert r.status_code == 200
        assert r.json()["1"] == []
