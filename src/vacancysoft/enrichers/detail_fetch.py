"""
Detail fetcher — back-fills missing Date Posted and Location from individual job pages.

Routing:
  - Workday         → CXS detail API  (startDate + locations fields)
  - SmartRecruiters → posting detail API
  - Everything else → Playwright page + multi-strategy extraction

Ported from the old project's scrapers/detail_scraper.py and adapted to the
new project's enrichment pipeline and browser session management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

from vacancysoft.browser.session import browser_session
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import normalise_location

logger = logging.getLogger(__name__)

PAGE_TIMEOUT = 15_000
_HTTP_TIMEOUT = 20.0

# ──────────────────────────────────────────────────────────────────────────────
# Workday CXS detail API
# ──────────────────────────────────────────────────────────────────────────────

def _workday_api_url(page_url: str) -> str | None:
    """
    Convert a Workday job page URL to the CXS detail API URL.

    Page:  https://athene.wd5.myworkdayjobs.com/en-US/Apollo_Careers/job/City/Title_R123
    API:   https://athene.wd5.myworkdayjobs.com/wday/cxs/athene/Apollo_Careers/job/City/Title_R123
    """
    parsed = urlparse(page_url)
    host = parsed.netloc.lower()
    path = parsed.path

    parts = [p for p in path.split("/") if p]
    if len(parts) < 3 or parts[2] != "job":
        return None

    if "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        site = parts[1]
    elif "myworkdaysite.com" in host:
        if len(parts) < 4:
            return None
        tenant = parts[2]
        site = parts[3]
    else:
        return None

    job_path = "/" + "/".join(parts[2:])
    return f"https://{host}/wday/cxs/{tenant}/{site}{job_path}"


async def _fetch_workday_detail(url: str, client: httpx.AsyncClient) -> dict:
    """Fetch date and location from Workday CXS detail endpoint."""
    result: dict[str, str | None] = {"date": None, "location": None}
    api_url = _workday_api_url(url)
    if not api_url:
        return result
    try:
        resp = await client.get(api_url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return result
        data = resp.json()
    except Exception as exc:
        logger.debug("Workday detail API failed for %s: %s", url, exc)
        return result

    info = data.get("jobPostingInfo") or data

    # Date: prefer startDate (ISO) over postedOn (relative)
    raw_date = info.get("startDate") or info.get("postedOn") or ""
    if raw_date:
        result["date"] = raw_date

    # Location: list of location dicts with 'name' key
    locs = info.get("locations") or []
    if locs:
        result["location"] = ", ".join(
            loc.get("name", "") for loc in locs if loc.get("name")
        )
    elif info.get("location"):
        result["location"] = info["location"]

    return result


# ──────────────────────────────────────────────────────────────────────────────
# SmartRecruiters detail API
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_smartrecruiters_detail(url: str, client: httpx.AsyncClient) -> dict:
    """Fetch date and location from SmartRecruiters posting API."""
    result: dict[str, str | None] = {"date": None, "location": None}
    path_parts = urlparse(url).path.strip("/").split("/")
    if len(path_parts) < 2:
        return result
    slug = path_parts[1]
    job_id = slug.split("-")[0]
    company = path_parts[0]

    api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}"
    try:
        resp = await client.get(api_url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return result
        data = resp.json()
    except Exception as exc:
        logger.debug("SmartRecruiters detail API failed for %s: %s", url, exc)
        return result

    if data.get("releasedDate"):
        result["date"] = data["releasedDate"]

    # Location from the posting
    loc = data.get("location") or {}
    parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
    loc_str = ", ".join(p for p in parts if p)
    if loc_str:
        result["location"] = loc_str

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Playwright-based extraction (universal fallback)
# ──────────────────────────────────────────────────────────────────────────────

# Date extraction strategies
_JSON_LD_DATE_KEYS = ["datePosted", "dateCreated", "dateModified", "datePublished"]

_META_DATE_PROPS = [
    "article:published_time", "article:modified_time",
    "og:updated_time", "og:published_time",
    "date", "pubdate", "publish-date", "DC.date",
]

_DATE_PATTERNS = [
    r"\b(\d{4}-\d{2}-\d{2})(?:T|\b)",
    r"(?:posted|published|date)[:\s]+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
    r"(?:posted|published|date)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    r"\b(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\b",
    r"\b([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\b",
]
_DATE_RE = [re.compile(p, re.IGNORECASE) for p in _DATE_PATTERNS]


def _extract_date_from_json_ld(html: str) -> str:
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            obj = json.loads(block)
            if isinstance(obj, list):
                obj = obj[0]
            for key in _JSON_LD_DATE_KEYS:
                val = obj.get(key, "")
                if val:
                    return str(val)
        except Exception:
            pass
    return ""


def _extract_date_from_meta(html: str) -> str:
    for prop in _META_DATE_PROPS:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\'](?:{re.escape(prop)})["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if m:
            return m.group(1)
    return ""


def _extract_date_from_time_elements(html: str) -> str:
    m = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_date_from_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    for pattern in _DATE_RE:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return ""


# Location extraction strategies

def _extract_location_from_json_ld(html: str) -> str:
    """Parse schema.org JobPosting → jobLocation → address."""
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            obj = json.loads(block)
            if isinstance(obj, list):
                obj = obj[0]
            if obj.get("@type") not in ("JobPosting", "Job"):
                continue
            loc = obj.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0]
            addr = loc.get("address") or {}
            if isinstance(addr, str):
                return addr.strip()
            parts = [
                addr.get("addressLocality", ""),
                addr.get("addressRegion", ""),
                addr.get("addressCountry", ""),
            ]
            result = ", ".join(p.strip() for p in parts if p.strip())
            if result:
                return result
        except Exception:
            pass
    return ""


def _extract_location_from_meta(html: str) -> str:
    for prop in ["og:location", "job-location", "location"]:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\'](?:{re.escape(prop)})["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
    return ""


_LOCATION_PATTERNS = [
    r'(?:location|based in|office)[:\s]*[–\-]?\s*([A-Z][A-Za-z\s,]+(?:UK|London|New York|NYC|Dublin|Paris|Frankfurt|Amsterdam|Zurich|Toronto|Singapore)[A-Za-z\s,]*)',
    r'📍\s*([A-Za-z][A-Za-z\s,]+)',
]
_LOCATION_RE = [re.compile(p, re.IGNORECASE) for p in _LOCATION_PATTERNS]


def _extract_location_from_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    for pattern in _LOCATION_RE:
        m = pattern.search(text)
        if m:
            loc = m.group(1).strip().strip(",").strip()
            if len(loc) < 100:
                return loc
    return ""


async def _fetch_detail_from_page(url: str) -> dict:
    """Load the job page in Playwright and extract date + location from HTML."""
    result: dict[str, str | None] = {"date": None, "location": None}
    try:
        async with async_playwright() as pw:
            async with browser_session(pw) as (_browser, context):
                page = await context.new_page()
                try:
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                    except Exception:
                        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                        await asyncio.sleep(3)

                    html = await page.content()
                finally:
                    await page.close()

        # Extract date
        for extractor in [
            _extract_date_from_json_ld,
            _extract_date_from_time_elements,
            _extract_date_from_meta,
            _extract_date_from_text,
        ]:
            raw_date = extractor(html)
            if raw_date:
                result["date"] = raw_date
                break

        # Extract location
        for extractor in [
            _extract_location_from_json_ld,
            _extract_location_from_meta,
            _extract_location_from_text,
        ]:
            loc = extractor(html)
            if loc:
                result["location"] = loc
                break

    except Exception as exc:
        logger.warning("Page detail scrape failed for %s: %s", url, exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Main dispatcher
# ──────────────────────────────────────────────────────────────────────────────

async def fetch_job_detail(
    url: str,
    client: httpx.AsyncClient | None = None,
    *,
    use_browser: bool = False,
) -> dict:
    """
    Dispatch to the right strategy based on URL domain.
    Returns {"date": str|None, "location": str|None}.

    Set *use_browser=True* to enable the slow Playwright fallback for
    platforms without a known API.  When False (the default), only fast
    HTTP-based strategies are tried.
    """
    domain = urlparse(url).netloc.lower()
    own_client = client is None

    if own_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True)

    try:
        # Workday — fast API call, no browser needed
        if "myworkdayjobs.com" in domain or "myworkdaysite.com" in domain:
            result = await _fetch_workday_detail(url, client)
            if result.get("date") or result.get("location"):
                return result

        # SmartRecruiters — fast API call
        if "smartrecruiters.com" in domain:
            result = await _fetch_smartrecruiters_detail(url, client)
            if result.get("date") or result.get("location"):
                return result

        # Playwright fallback — only if explicitly requested (slow)
        if use_browser:
            return await _fetch_detail_from_page(url)

        return {"date": None, "location": None}

    finally:
        if own_client:
            await client.aclose()
