from enum import StrEnum


class SourceType(StrEnum):
    ATS_API = "ats_api"
    ATS_HTML = "ats_html"
    BROWSER_SITE = "browser_site"
    HYBRID = "hybrid"
    DISCOVERED_CANDIDATE = "discovered_candidate"
