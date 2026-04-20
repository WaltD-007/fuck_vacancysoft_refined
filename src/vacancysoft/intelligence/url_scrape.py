"""Thin wrapper around the Playwright runner's /scrape endpoint.

The runner lives in a separate Node service (../playwright-runner). It accepts
POST {url, workday?} and returns a dict with the canonical shape documented on
the paste-lead route: status, title, company, location, description,
postedDate, selectorUsed, finalUrl, error. Every field is always present;
empty-string sentinels replace missing values.

Callers:
  - api/routes/leads.py            _scrape_and_generate_dossier (in-process
                                   fallback when Redis is unavailable)
  - api/routes/leads.py            paste_lead (manual URL paste)
  - worker/tasks.py                process_lead (queued dossier generation)
  - scripts/backfill_enriched_location.py (future one-off)

Centralising the call in one place means adapter-specific tweaks (Workday
config blob, future SuccessFactors hints, timeout tuning) only need changing
here.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_PLAYWRIGHT_SCRAPER_URL = (
    "https://playwright-runner.bluecliff-1ceb6690.uksouth.azurecontainerapps.io/scrape"
)


def _error_payload(url: str, message: str) -> dict[str, Any]:
    """Canonical empty-fields payload returned on network / runner error so
    every caller gets the same shape back and can branch on `status`."""
    return {
        "status": "error",
        "url": url,
        "finalUrl": url,
        "title": "",
        "company": "",
        "location": "",
        "description": "",
        "descriptionLength": 0,
        "wasTruncated": False,
        "postedDate": "",
        "selectorUsed": "",
        "error": message,
    }


async def scrape_advert(
    url: str,
    *,
    workday: dict[str, Any] | None = None,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Scrape a job advert URL via the Playwright runner.

    Returns a dict with keys:
      status:          "success" | "empty" | "content_blocked" | "error"
      title, company, location, description, postedDate  (all strings,
                                                          empty when missing)
      descriptionLength, wasTruncated                    (bookkeeping)
      selectorUsed, finalUrl                             (diagnostics)
      error:           null on success, error message otherwise

    Never raises. Network / HTTP failures are reflected in `status="error"`
    with an explanatory `error` message so callers can make their own
    decisions (return 422 to the UI vs. silently skip).
    """
    body: dict[str, Any] = {"url": url}
    if workday:
        body["workday"] = workday

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(_PLAYWRIGHT_SCRAPER_URL, json=body)
    except httpx.HTTPError as exc:
        logger.warning("scrape_advert network error for %s: %s", url, exc)
        return _error_payload(url, f"Playwright runner unreachable: {exc}")

    if resp.status_code != 200:
        # Runner itself returned a non-200 (500 path or a 400 from missing url
        # somehow). Surface the body as the error so it shows up in logs.
        snippet = (resp.text or "")[:300]
        logger.warning(
            "scrape_advert runner returned %s for %s: %s",
            resp.status_code, url, snippet,
        )
        return _error_payload(url, f"Playwright runner HTTP {resp.status_code}: {snippet}")

    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("scrape_advert non-JSON response for %s: %s", url, exc)
        return _error_payload(url, f"Playwright runner returned non-JSON: {exc}")

    # Belt-and-braces — ensure every key the caller expects is present even if
    # the runner is running an older deploy that omits some fields.
    for key in (
        "status", "url", "finalUrl", "title", "company", "location",
        "description", "postedDate", "selectorUsed", "error",
    ):
        data.setdefault(key, "" if key != "error" else None)
    data.setdefault("descriptionLength", len(data.get("description") or ""))
    data.setdefault("wasTruncated", False)
    return data
