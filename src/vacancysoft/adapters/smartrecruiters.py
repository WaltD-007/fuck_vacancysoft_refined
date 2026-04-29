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

API_BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100
DEFAULT_SEARCH_TERMS = ["risk", "quant", "quantitative", "compliance", "strats", "pricing"]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_posting(posting: dict[str, Any], board: dict[str, Any]) -> DiscoveredJobRecord:
    location_obj = posting.get("location") or {}
    location_parts = []
    if isinstance(location_obj, dict):
        location_parts = [_clean(location_obj.get("city")), _clean(location_obj.get("region")), _clean(location_obj.get("country"))]
    location = ", ".join(part for part in location_parts if part) or None
    discovered_url = f"https://jobs.smartrecruiters.com/{board['slug']}/{posting.get('id', '')}" if posting.get("id") else board["url"]
    contract_type = _clean(((posting.get("typeOfEmployment") or {}).get("label") if isinstance(posting.get("typeOfEmployment"), dict) else None))
    company_name = lookup_company("smartrecruiters", board_url=board.get("url"), slug=board.get("slug"), explicit_company=board.get("company"))
    completeness_fields = [_clean(posting.get("name")), location, discovered_url, _clean(posting.get("releasedDate"))]
    completeness_score = sum(1 for value in completeness_fields if value) / len(completeness_fields)

    return DiscoveredJobRecord(
        external_job_id=_clean(posting.get("id")) or discovered_url,
        title_raw=_clean(posting.get("name")),
        location_raw=location,
        posted_at_raw=_clean(posting.get("releasedDate")),
        summary_raw=contract_type,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload=posting,
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.94,
        provenance={
            "adapter": "smartrecruiters",
            "method": ExtractionMethod.API.value,
            "company": company_name or "",
            "platform": "SmartRecruiters",
            "board_url": str(board.get("url") or ""),
            "board_slug": str(board.get("slug") or ""),
            "contract_type": contract_type,
        },
    )


class SmartRecruitersAdapter(SourceAdapter):
    adapter_name = "smartrecruiters"
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
        complete_coverage_per_run=True,
    )

    async def discover(self, source_config: dict[str, Any], cursor: str | None = None, since: datetime | None = None, on_page_scraped: PageCallback = None) -> DiscoveryPage:
        slug = str(source_config.get("slug") or "").strip()
        if not slug:
            raise ValueError("SmartRecruiters source_config requires slug")

        search_terms = [str(term).strip() for term in (source_config.get("search_terms") or DEFAULT_SEARCH_TERMS) if str(term).strip()]
        board = {
            "slug": slug,
            "company": source_config.get("company"),
            "url": str(source_config.get("job_board_url") or f"https://jobs.smartrecruiters.com/{slug}").strip(),
        }
        diagnostics = AdapterDiagnostics(metadata={"slug": slug, "url": f"{API_BASE}/{slug}/postings"})
        if since is not None:
            diagnostics.warnings.append("SmartRecruitersAdapter does not enforce incremental sync at source. since was ignored.")

        if cursor:
            term_index_str, offset_str = cursor.split(":", 1)
            term_index = int(term_index_str)
            offset = int(offset_str)
        else:
            term_index = 0
            offset = 0

        all_records: list[DiscoveredJobRecord] = []
        seen_ids: set[str] = set()
        next_cursor: str | None = None
        async with httpx.AsyncClient(timeout=float(source_config.get("timeout_seconds", 20))) as client:
            for idx in range(term_index, len(search_terms)):
                term = search_terms[idx]
                current_offset = offset if idx == term_index else 0
                while True:
                    response = await client.get(
                        f"{API_BASE}/{slug}/postings",
                        params={"q": term, "limit": PAGE_SIZE, "offset": current_offset},
                    )
                    response.raise_for_status()
                    data = response.json()
                    postings = data.get("content") or []
                    diagnostics.counters["status_code"] = int(response.status_code)
                    diagnostics.counters["requests_made"] = diagnostics.counters.get("requests_made", 0) + 1
                    if not postings:
                        break
                    for posting in postings:
                        if not isinstance(posting, dict):
                            continue
                        posting_id = _clean(posting.get("id"))
                        if posting_id and posting_id in seen_ids:
                            diagnostics.counters["duplicates"] = diagnostics.counters.get("duplicates", 0) + 1
                            continue
                        if posting_id:
                            seen_ids.add(posting_id)
                        all_records.append(_parse_posting(posting, board))
                    total = int(data.get("totalFound") or 0)
                    current_offset += PAGE_SIZE
                    if current_offset >= total:
                        break
                    next_cursor = f"{idx}:{current_offset}"
                offset = 0

        diagnostics.counters["jobs_seen"] = len(all_records)
        return DiscoveryPage(jobs=all_records, next_cursor=next_cursor, diagnostics=diagnostics)
