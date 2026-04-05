from __future__ import annotations

from datetime import datetime


def parse_posted_date(value: str | None) -> datetime | None:
    if not value:
        return None

    cleaned = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None
