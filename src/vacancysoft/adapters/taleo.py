from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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


def _extract_career_section_id(url: str) -> str:
    """Extract the career section number from a Taleo URL.

    e.g. https://voyacareers.taleo.net/careersection/2/jobsearch.ftl -> 2
    """
    match = re.search(r"/careersection/(\d+)/", url)
    return match.group(1) if match else "2"


def _build_api_base(board_url: str) -> str:
    """Build the REST API base URL from a Taleo board URL."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_job_url(api_base: str, career_section: str, job_id: str | int) -> str:
    return f"{api_base}/careersection/{career_section}/jobdetail.ftl?job={job_id}"


def _parse_search_result(
    row: dict[str, Any],
    company_name: str,
    board_url: str,
    api_base: str,
    career_section: str,
) -> DiscoveredJobRecord | None:
    # Taleo search results contain a "column" list with positional values
    # Typical columns: Title, Location, Date, Job Number
    columns = row.get("column") or []
    if not columns:
        return None

    title = None
    location = None
    posted_at = None
    job_number = None

    for col in columns:
        if not isinstance(col, dict):
            continue
        val = _clean(col.get("value"))
        col_id = _clean(col.get("id") or col.get("name") or "")
        if not val:
            continue

        col_lower = (col_id or "").lower()
        if "title" in col_lower or "jobtitle" in col_lower:
            title = val
        elif "location" in col_lower or "loc" in col_lower:
            location = val
        elif "date" in col_lower or "posted" in col_lower:
            posted_at = val
        elif "number" in col_lower or "req" in col_lower or "id" in col_lower:
            job_number = val

    # Fallback: use positional columns if named matching failed
    if not title and len(columns) >= 1:
        title = _clean(columns[0].get("value"))
    if not location and len(columns) >= 2:
        location = _clean(columns[1].get("value"))
    if not posted_at and len(columns) >= 3:
        posted_at = _clean(columns[2].get("value"))

    if not title or len(title) < 4:
        return None

    # Job URL from contestNo or row number
    contest_no = _clean(row.get("contestNo")) or job_number
    job_url = None
    if contest_no:
        job_url = _build_job_url(api_base, career_section, contest_no)

    completeness_fields = [title, location, job_url, posted_at]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=contest_no or job_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=posted_at,
        summary_raw=None,
        discovered_url=job_url,
        apply_url=job_url,
        listing_payload=row,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.90,
        provenance={
            "adapter": "taleo",
            "method": ExtractionMethod.API.value,
            "company": company_name,
            "platform": "Taleo Enterprise",
            "board_url": board_url,
            "career_section": career_section,
        },
    )


# Default search payload for Taleo Enterprise career section REST API
def _build_search_payload(page_no: int = 1, keyword: str = "") -> dict:
    return {
        "multilineEnabled": False,
        "sortingSelection": {
            "sortBySelectionParam": "3",
            "ascendingSortingOrder": "false",
        },
        "fieldData": {
            "fields": {"KEYWORD": keyword, "LOCATION": ""},
            "valid": True,
        },
        "filterSelectionParam": {
            "searchFilterSelections": [
                {"id": "LOCATION", "selectedValues": []},
                {"id": "JOB_FIELD", "selectedValues": []},
                {"id": "ORGANIZATION", "selectedValues": []},
                {"id": "JOB_SCHEDULE", "selectedValues": []},
                {"id": "JOB_TYPE", "selectedValues": []},
                {"id": "JOB_LEVEL", "selectedValues": []},
                {"id": "POSTING_DATE", "selectedValues": []},
                {"id": "WILL_TRAVEL", "selectedValues": []},
            ]
        },
        "advancedSearchFiltersSelectionParam": {
            "searchFilterSelections": [
                {"id": "JOB_NUMBER", "selectedValues": []},
                {"id": "JOB_SHIFT", "selectedValues": []},
                {"id": "URGENT_JOB", "selectedValues": []},
                {"id": "STUDY_LEVEL", "selectedValues": []},
            ]
        },
        "pageNo": page_no,
    }


class TaleoAdapter(SourceAdapter):
    adapter_name = "taleo"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=True,
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
            raise ValueError("Taleo source_config requires job_board_url")

        company_name = lookup_company(
            "taleo",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )
        career_section = _extract_career_section_id(board_url)
        api_base = _build_api_base(board_url)
        api_url = f"{api_base}/careersection/rest/jobboard/searchjobs?lang=en&portal={career_section}"
        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        max_pages = int(source_config.get("max_pages", 10))
        diagnostics = AdapterDiagnostics(
            metadata={"board_url": board_url, "api_url": api_url, "career_section": career_section}
        )
        t0 = time.monotonic()

        if since is not None:
            diagnostics.warnings.append("TaleoAdapter does not enforce incremental sync at source. since was ignored.")

        records: list[DiscoveredJobRecord] = []
        page_no = int(cursor) if cursor else 1
        next_cursor: str | None = None

        headers = {
            "Content-Type": "application/json",
            "tz": "GMT+00:00",
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for _ in range(max_pages):
                payload = _build_search_payload(page_no=page_no)
                try:
                    response = await client.post(api_url, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                    diagnostics.errors.append(f"API request failed on page {page_no}: {exc}")
                    break

                diagnostics.counters["status_code"] = int(response.status_code)

                # Extract job rows from the response
                requisition_list = data.get("requisitionList") or []
                if not requisition_list:
                    break

                page_records = []
                for row in requisition_list:
                    if not isinstance(row, dict):
                        continue
                    rec = _parse_search_result(row, company_name, board_url, api_base, career_section)
                    if rec:
                        page_records.append(rec)

                records.extend(page_records)
                diagnostics.counters[f"page_{page_no}_jobs"] = len(page_records)

                if on_page_scraped:
                    await on_page_scraped(page_no, page_records, records)

                # Check if there are more pages
                total_count = data.get("totalCount") or data.get("jobCount") or 0
                if len(records) >= total_count or not page_records:
                    break

                page_no += 1
                next_cursor = str(page_no)

        # Clear next_cursor if we've exhausted all pages
        if not records or len(records) >= (diagnostics.metadata.get("total_count", 0) or len(records)):
            next_cursor = None

        diagnostics.counters["total_jobs"] = len(records)
        diagnostics.counters["pages_fetched"] = page_no
        diagnostics.timings_ms["discover"] = round((time.monotonic() - t0) * 1000)

        return DiscoveryPage(jobs=records, next_cursor=next_cursor, diagnostics=diagnostics)
