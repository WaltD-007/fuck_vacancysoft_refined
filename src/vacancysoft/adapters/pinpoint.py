from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from vacancysoft.adapters.base import (
    AdapterCapabilities,
    AdapterDiagnostics,
    DiscoveredJobRecord,
    DiscoveryPage,
    ExtractionMethod,
    PageCallback,
    SourceAdapter,
)
from vacancysoft.source_registry.legacy_board_mappings import lookup_company


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_job(job: dict[str, Any], board_url: str, company_name: str) -> DiscoveredJobRecord:
    title = _clean(job.get("title"))
    loc_obj = job.get("location")
    if isinstance(loc_obj, dict):
        location = _clean(loc_obj.get("name") or loc_obj.get("city"))
    else:
        location = _clean(loc_obj)
    job_id = _clean(job.get("id"))
    posted_at = _clean(job.get("published_at") or job.get("created_at"))

    # Build URL from board domain + posting ID
    base = board_url.rstrip("/").replace("/en/postings", "").replace("/postings", "")
    discovered_url = f"{base}/en/postings/{job_id}" if job_id else None

    salary = None
    sal_min = job.get("compensation_minimum")
    sal_max = job.get("compensation_maximum")
    sal_currency = _clean(job.get("compensation_currency")) or ""
    if sal_min and sal_max and job.get("compensation_visible"):
        salary = f"{sal_currency}{int(sal_min):,} - {sal_currency}{int(sal_max):,}".strip()

    department = _clean(job.get("department"))
    employment_type = _clean(job.get("employment_type"))
    summary_parts = [p for p in [salary, department, employment_type] if p]
    summary_raw = " | ".join(summary_parts) if summary_parts else None

    completeness_fields = [title, location, discovered_url, posted_at]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=job_id or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=job,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.95,
        provenance={
            "adapter": "pinpoint",
            "method": ExtractionMethod.API.value,
            "company": company_name,
            "platform": "Pinpoint",
            "board_url": board_url,
            "salary": salary,
            "department": department,
        },
    )


class PinpointAdapter(SourceAdapter):
    adapter_name = "pinpoint"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=True,
        supports_html=False,
        supports_browser=False,
        supports_site_rescue=False,
    )

    async def discover(
        self,
        source_config: dict[str, Any],
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback = None,
    ) -> DiscoveryPage:
        board_url = str(source_config.get("job_board_url") or "").strip()
        if not board_url:
            raise ValueError("Pinpoint source_config requires job_board_url")

        company_name = lookup_company(
            "pinpoint",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )

        # Build JSON API URL from board URL — strip hash fragments and query params
        json_url = board_url.split("#")[0].split("?")[0].rstrip("/")
        if not json_url.endswith(".json"):
            json_url = json_url + ".json"

        timeout_seconds = float(source_config.get("timeout_seconds", 20))
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "json_url": json_url})

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(json_url)
            response.raise_for_status()
            data = response.json()

        diagnostics.counters["status_code"] = int(response.status_code)

        jobs_list = data.get("data") or data if isinstance(data, list) else []
        if isinstance(data, dict) and "data" in data:
            jobs_list = data["data"]

        records = [_parse_job(job, board_url, company_name) for job in jobs_list if isinstance(job, dict)]
        diagnostics.counters["jobs_seen"] = len(records)

        if on_page_scraped and records:
            try:
                on_page_scraped(1, records, records)
            except Exception:
                pass

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
