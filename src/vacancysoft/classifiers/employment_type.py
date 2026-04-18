from __future__ import annotations

import re

PERMANENT = "Permanent"
CONTRACT = "Contract"

CONTRACT_TERMS: tuple[str, ...] = (
    "12 Month Contract",
    "6 Month Contract",
    "Contract Basis",
    "Contractor",
    "Daily Rate",
    "Day Rate",
    "Fixed Term",
    "FTC",
    "Hour Rate",
    "Hourly Rate",
    "Interim",
    "Long Term Contract",
    "Mat Cover",
    "Mat Leave",
    "Maternity Cover",
    "Ongoing Contract",
    "P/d",
    "P/h",
    "Per Day",
    "Per Hour",
    "Rolling Contract",
    "Secondment",
    "Short Term Contract",
    "Six Month Contract",
    "Temp",
    "Temporary",
    "Twelve Month Contract",
)

_CONTRACT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in CONTRACT_TERMS) + r")\b",
    re.IGNORECASE,
)


def classify_employment_type(title: str | None) -> str:
    if not title:
        return PERMANENT
    return CONTRACT if _CONTRACT_RE.search(title) else PERMANENT
