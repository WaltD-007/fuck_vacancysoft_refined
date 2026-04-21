from __future__ import annotations

from datetime import datetime
from hashlib import sha1
from typing import Any

from vacancysoft.exporters.legacy_mapping import load_legacy_routing, map_category, map_sub_specialism, normalise_country

LEGACY_EXPORT_COLUMNS = [
    "Job URL",
    "Job Title",
    "Job Ref",
    "Category",
    "Sub Specialism",
    "Company",
    "Location",
    "Country",
    "Salary",
    "Contract Type",
    "Date Posted",
    "Job Board URL",
    "Platform",
    "Date Scraped",
]


def _slug(value: str, max_len: int = 24) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed[:max_len] or "value"


def _hash_string(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:10]


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item is not None)
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _build_job_ref(company: str, title: str, location: str, country: str, job_url: str, date_posted: str, platform: str) -> str:
    fingerprint = "|".join([job_url, company, title, location, country, date_posted, platform])
    return f"lead-{_slug(company or 'company')}-{_slug(title or 'role')}-{_hash_string(fingerprint)}"


_ROUTING_CACHE: dict[str, Any] | None = None


def _cached_routing() -> dict[str, Any]:
    global _ROUTING_CACHE
    if _ROUTING_CACHE is None:
        _ROUTING_CACHE = load_legacy_routing()
    return _ROUTING_CACHE


def row_to_legacy_lead(row: Any) -> dict[str, str]:
    routing = _cached_routing()
    mapping = row._mapping if hasattr(row, "_mapping") else dict(row)

    title = _safe_str(mapping.get("title"))
    location_city = _safe_str(mapping.get("location_city"))
    location_text = location_city or _safe_str(mapping.get("location_text"))
    raw_country = _safe_str(mapping.get("location_country"))
    company = _safe_str(mapping.get("employer_name"))
    job_url = _safe_str(mapping.get("discovered_url"))
    apply_url = _safe_str(mapping.get("apply_url"))
    source_key = _safe_str(mapping.get("source_key"))
    date_posted = _safe_str(mapping.get("posted_at"))
    # Previously `datetime.now().date().isoformat()`, which made every
    # row show the export run time — not useful if the operator wants
    # to know when a lead actually entered the pipeline. Now sourced
    # from RawJob.first_seen_at (added to the base query's SELECT).
    # _safe_str formats datetimes to ISO-date strings; falls back to
    # empty string if the value is missing (shouldn't happen for
    # export-eligible rows but defensive).
    date_scraped = _safe_str(mapping.get("first_seen_at"))
    platform = source_key
    job_board_url = apply_url or job_url
    salary = ""
    contract_type = ""

    category = map_category(_safe_str(mapping.get("primary_taxonomy_key")) or None, routing)
    sub_specialism = map_sub_specialism(title=title, category=category, routing=routing)
    country = normalise_country(raw_country, routing)

    job_ref = _build_job_ref(
        company=company,
        title=title,
        location=location_text,
        country=country,
        job_url=job_url,
        date_posted=date_posted,
        platform=platform,
    )

    return {
        "Job URL": job_url,
        "Job Title": title,
        "Job Ref": job_ref,
        "Category": category,
        "Sub Specialism": sub_specialism,
        "Company": company,
        "Location": location_text,
        "Country": country,
        "Salary": salary,
        "Contract Type": contract_type,
        "Date Posted": date_posted,
        "Job Board URL": job_board_url,
        "Platform": platform,
        "Date Scraped": date_scraped,
    }


def build_legacy_webhook_payload(rows: list[Any]) -> dict[str, list[dict[str, str]]]:
    return {"body": [row_to_legacy_lead(row) for row in rows]}
