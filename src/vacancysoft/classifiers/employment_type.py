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
    "FTC",
    "Half Year Contract",
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

# Generalised regex patterns to catch numeric and structural variants
# the literal list can't enumerate exhaustively. Each pattern is tightly
# scoped to avoid catching permanent roles whose title merely mentions
# the word "contract" (e.g. "Contracts Manager", "Contract Law").
_GENERAL_PATTERNS: tuple[str, ...] = (
    # "8 Month Contract", "14-Month Contract", "12 months contract"
    r"\b\d+\s*-?\s*month[s]?\s+contract\b",
    # "1 Year Contract", "2-year contract"
    r"\b\d+\s*-?\s*year[s]?\s+contract\b",
    # "6m Contract", "12m Contract"
    r"\b\d+m\s+contract\b",
    # "(Contract)" or "[Contract]" — bracket boundaries on both sides
    # so "Contracts Manager" / "(Contract Position)" do NOT match.
    r"[\(\[]\s*contract\s*[\)\]]",
    # Inverse order: "Contract (14 Months)", "Contract [12 Month]"
    r"\bcontract\s*[\(\[]\s*\d+\s*-?\s*month[s]?\s*[\)\]]",
    # Trailing "- Contract" at end of title (en-dash and hyphen).
    # Anchored to end-of-string so "- Contract Manager" does NOT match.
    r"[\-\u2013]\s+contract\s*$",
    # "Fixed Term" / "Fixed-Term" — supersedes the literal in CONTRACT_TERMS
    # so both spaced and hyphenated variants are caught.
    r"\bfixed[-\s]+term\b",
    # "Limited to N months" / "Limited to 24 month" — explicit time-limit
    # phrasing without the word "contract".
    r"\blimited\s+to\s+\d+\s*-?\s*month[s]?\b",
)

_CONTRACT_RE = re.compile(
    "|".join(
        [r"\b(?:" + "|".join(re.escape(term) for term in CONTRACT_TERMS) + r")\b"]
        + list(_GENERAL_PATTERNS)
    ),
    re.IGNORECASE,
)


def classify_employment_type(title: str | None) -> str:
    if not title:
        return PERMANENT
    return CONTRACT if _CONTRACT_RE.search(title) else PERMANENT
