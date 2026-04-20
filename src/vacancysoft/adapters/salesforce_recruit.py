from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

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

# Salesforce Recruit (fRecruit) portals use Salesforce Sites.
# Pattern: https://{org}.my.salesforce-sites.com/recruit/fRecruit__ApplyJobList?portal=...
# Individual jobs: .../fRecruit__ApplyJob?vacancyNo=VN1234&portal=...


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_base_url(board_url: str) -> str:
    """Extract the base site URL up to /recruit/."""
    m = re.match(r"(https?://[^/]+/recruit/)", board_url)
    return m.group(1) if m else board_url.rstrip("/") + "/"


def _extract_portal(board_url: str) -> str | None:
    """Extract portal parameter from URL."""
    m = re.search(r"[?&]portal=([^&]+)", board_url)
    return m.group(1) if m else None


def _parse_job_from_row(
    row: Any,
    base_url: str,
    portal: str | None,
    company_name: str,
    board_url: str,
) -> DiscoveredJobRecord | None:
    cells = row.find_all("td")
    if not cells:
        return None

    # Try to find the link to the job posting
    link = row.find("a", href=True)
    title = None
    discovered_url = None
    vacancy_no = None

    if link:
        title = _clean(link.get_text())
        href = link["href"]
        if href.startswith("/"):
            # Relative URL — build from base domain
            m = re.match(r"(https?://[^/]+)", base_url)
            domain = m.group(1) if m else ""
            discovered_url = domain + href
        elif href.startswith("http"):
            discovered_url = href
        else:
            discovered_url = base_url + href

        vn_match = re.search(r"vacancyNo=([^&]+)", discovered_url or "")
        if vn_match:
            vacancy_no = vn_match.group(1)

    if not title:
        title = _clean(cells[0].get_text()) if cells else None

    location = _clean(cells[1].get_text()) if len(cells) > 1 else None
    department = _clean(cells[2].get_text()) if len(cells) > 2 else None

    completeness_fields = [title, location, discovered_url, vacancy_no]
    completeness_score = sum(1 for v in completeness_fields if v) / len(completeness_fields)

    summary_parts = [p for p in [department] if p]
    summary_raw = " | ".join(summary_parts) if summary_parts else None

    return DiscoveredJobRecord(
        external_job_id=vacancy_no or discovered_url or title,
        title_raw=title,
        location_raw=location,
        posted_at_raw=None,
        summary_raw=summary_raw,
        discovered_url=discovered_url,
        apply_url=discovered_url,
        listing_payload={"vacancy_no": vacancy_no, "department": department},
        completeness_score=round(completeness_score, 4),
        extraction_confidence=0.80,
        provenance={
            "adapter": "salesforce_recruit",
            "method": ExtractionMethod.HTML.value,
            "company": company_name,
            "platform": "Salesforce Recruit (fRecruit)",
            "board_url": board_url,
            "portal": portal,
            "department": department,
        },
    )


class SalesforceRecruitAdapter(SourceAdapter):
    adapter_name = "salesforce_recruit"
    capabilities = AdapterCapabilities(
        supports_discovery=True,
        supports_detail_fetch=False,
        supports_healthcheck=False,
        supports_pagination=False,
        supports_incremental_sync=False,
        supports_api=False,
        supports_html=True,
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
            raise ValueError("SalesforceRecruit source_config requires job_board_url")

        company_name = lookup_company(
            "salesforce_recruit",
            board_url=board_url,
            slug=source_config.get("slug"),
            explicit_company=source_config.get("company"),
        )

        base_url = _extract_base_url(board_url)
        portal = _extract_portal(board_url)

        timeout_seconds = float(source_config.get("timeout_seconds", 30))
        diagnostics = AdapterDiagnostics(metadata={"board_url": board_url, "portal": portal})

        if cursor is not None:
            diagnostics.warnings.append("SalesforceRecruitAdapter does not support pagination. cursor was ignored.")
        if since is not None:
            diagnostics.warnings.append("SalesforceRecruitAdapter does not support incremental sync. since was ignored.")

        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(board_url)
            response.raise_for_status()
            html = response.text

        diagnostics.counters["status_code"] = int(response.status_code)

        soup = BeautifulSoup(html, "html.parser")

        # fRecruit portals render jobs in a table; find all table rows with job links
        records: list[DiscoveredJobRecord] = []
        table = soup.find("table")
        if table:
            rows = table.find_all("tr")
            for row in rows:
                # Skip header rows
                if row.find("th"):
                    continue
                rec = _parse_job_from_row(row, base_url, portal, company_name, board_url)
                if rec and rec.title_raw:
                    records.append(rec)

        # Fallback: if no table, look for job links matching fRecruit pattern
        if not records:
            links = soup.find_all("a", href=re.compile(r"fRecruit__ApplyJob|vacancyNo=", re.I))
            for link in links:
                title = _clean(link.get_text())
                if not title:
                    continue
                href = link["href"]
                if href.startswith("/"):
                    m = re.match(r"(https?://[^/]+)", base_url)
                    domain = m.group(1) if m else ""
                    discovered_url = domain + href
                elif href.startswith("http"):
                    discovered_url = href
                else:
                    discovered_url = base_url + href

                vn_match = re.search(r"vacancyNo=([^&]+)", discovered_url)
                vacancy_no = vn_match.group(1) if vn_match else None

                records.append(DiscoveredJobRecord(
                    external_job_id=vacancy_no or discovered_url,
                    title_raw=title,
                    location_raw=None,
                    posted_at_raw=None,
                    summary_raw=None,
                    discovered_url=discovered_url,
                    apply_url=discovered_url,
                    listing_payload={"vacancy_no": vacancy_no},
                    completeness_score=0.5,
                    extraction_confidence=0.70,
                    provenance={
                        "adapter": "salesforce_recruit",
                        "method": ExtractionMethod.HTML.value,
                        "company": company_name,
                        "platform": "Salesforce Recruit (fRecruit)",
                        "board_url": board_url,
                        "portal": portal,
                    },
                ))

        diagnostics.counters["jobs_seen"] = len(records)

        if on_page_scraped and records:
            try:
                on_page_scraped(1, records, records)
            except Exception:
                pass

        return DiscoveryPage(jobs=records, next_cursor=None, diagnostics=diagnostics)
