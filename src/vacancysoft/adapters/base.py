from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ExtractionMethod(str, Enum):
    API = "api"
    HTML = "html"
    BROWSER = "browser"
    SITE_RESCUE = "site_rescue"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"
    NEEDS_REVIEW = "needs_review"


@dataclass(slots=True)
class AdapterCapabilities:
    supports_discovery: bool = True
    supports_detail_fetch: bool = True
    supports_healthcheck: bool = True
    supports_pagination: bool = False
    supports_incremental_sync: bool = False
    supports_api: bool = False
    supports_html: bool = False
    supports_browser: bool = False
    supports_site_rescue: bool = False


@dataclass(slots=True)
class AdapterDiagnostics:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveredJobRecord:
    external_job_id: str | None
    title_raw: str | None
    location_raw: str | None
    posted_at_raw: str | None
    summary_raw: str | None
    discovered_url: str | None
    apply_url: str | None
    listing_payload: dict[str, Any] | None
    completeness_score: float
    extraction_confidence: float
    provenance: dict[str, Any]


@dataclass(slots=True)
class DiscoveryPage:
    jobs: list[DiscoveredJobRecord]
    next_cursor: str | None = None
    diagnostics: AdapterDiagnostics = field(default_factory=AdapterDiagnostics)


class SourceAdapter(ABC):
    adapter_name: str
    capabilities: AdapterCapabilities

    @abstractmethod
    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
    ) -> DiscoveryPage:
        raise NotImplementedError
