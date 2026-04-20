from __future__ import annotations

from pydantic import BaseModel, Field


class ClassificationPayload(BaseModel):
    enriched_job_id: str
    taxonomy_version: str
    primary_taxonomy_key: str | None = None
    secondary_taxonomy_keys: list[str] = Field(default_factory=list)
    sub_specialism: str | None = None
    sub_specialism_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    employment_type: str = "Permanent"
    title_relevance_score: float = Field(ge=0.0, le=1.0)
    classification_confidence: float = Field(ge=0.0, le=1.0)
    decision: str
    reasons: dict = Field(default_factory=dict)
