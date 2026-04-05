from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawJobCreate(BaseModel):
    source_id: int
    source_run_id: str
    extraction_attempt_id: str
    external_job_id: str | None = None
    canonical_url: str | None = None
    discovered_url: str | None = None
    apply_url: str | None = None
    title_raw: str | None = None
    location_raw: str | None = None
    posted_at_raw: str | None = None
    description_raw: str | None = None
    listing_payload: dict | None = None
    detail_payload: dict | None = None
    raw_text_blob: str | None = None
    job_fingerprint: str
    content_hash: str | None = None
    completeness_score: float = Field(ge=0.0, le=1.0)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    provenance_blob: dict
    discovery_ts: datetime
