from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


_DATE_FORMATS: list[str] = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
]

_RELATIVE_PATTERN = re.compile(
    r"(\d+)\s+(day|hour|minute|week|month)s?\s+ago", re.IGNORECASE
)


def parse_posted_date(value: str | None) -> datetime | None:
    if not value:
        return None

    cleaned = value.strip()

    # Try relative dates like "3 days ago", "1 hour ago"
    m = _RELATIVE_PATTERN.search(cleaned)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        now = datetime.now(tz=timezone.utc)
        if unit == "minute":
            return now - timedelta(minutes=amount)
        if unit == "hour":
            return now - timedelta(hours=amount)
        if unit == "day":
            return now - timedelta(days=amount)
        if unit == "week":
            return now - timedelta(weeks=amount)
        if unit == "month":
            return now - timedelta(days=amount * 30)

    # Strip trailing Z-offset duplicates that some APIs emit
    cleaned = re.sub(r"\+00:?00$", "", cleaned).rstrip("Z").strip()

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None
