from __future__ import annotations

from typing import Any

GENERIC_COMPANY_VALUES = {
    "apply",
    "boards",
    "careers",
    "greenhouse",
    "hcmui",
    "job boards",
    "jobs",
    "workable",
    "workday",
}

GREENHOUSE_BOARDS = [
    {"url": "https://job-boards.eu.greenhouse.io/mangroup/jobs", "company": "Man Group", "slug": "mangroup"},
    {"url": "https://boards.greenhouse.io/aztecgroup/jobs", "company": "Aztec Group", "slug": "aztecgroup"},
    {"url": "https://job-boards.eu.greenhouse.io/apollosyndicate1969/jobs", "company": "Apollo", "slug": "apollosyndicate1969"},
    {"url": "https://job-boards.eu.greenhouse.io/lhvuk/jobs", "company": "LHV", "slug": "lhvuk"},
    {"url": "https://job-boards.eu.greenhouse.io/policyexpert/jobs", "company": "Policy Expert", "slug": "policyexpert"},
    {"url": "https://job-boards.eu.greenhouse.io/srm/jobs", "company": "SRM", "slug": "srm"},
    {"url": "https://job-boards.eu.greenhouse.io/winton/jobs", "company": "Winton", "slug": "winton"},
    {"url": "https://job-boards.greenhouse.io/drweng/jobs", "company": "DRW Holdings", "slug": "drweng"},
    {"url": "https://job-boards.greenhouse.io/ibkr/jobs", "company": "Interactive Brokers", "slug": "ibkr"},
    {"url": "https://job-boards.greenhouse.io/mavensecuritiesholdingltd/jobs", "company": "Maven", "slug": "mavensecuritiesholdingltd"},
    {"url": "https://job-boards.greenhouse.io/pharomanagement/jobs", "company": "Pharo Management", "slug": "pharomanagement"},
    {"url": "https://job-boards.greenhouse.io/rothesaylife/jobs", "company": "Rothesay", "slug": "rothesaylife"},
    {"url": "https://boards.greenhouse.io/stripe/jobs", "company": "Stripe", "slug": "stripe"},
    {"url": "https://boards.greenhouse.io/coinbase/jobs", "company": "Coinbase", "slug": "coinbase"},
    {"url": "https://boards.greenhouse.io/chime/jobs", "company": "Chime", "slug": "chime"},
    {"url": "https://boards.greenhouse.io/robinhood/jobs", "company": "Robinhood", "slug": "robinhood"},
    {"url": "https://boards.greenhouse.io/monzo/jobs", "company": "Monzo", "slug": "monzo"},
    {"url": "https://boards.greenhouse.io/brex/jobs", "company": "Brex", "slug": "brex"},
    {"url": "https://boards.greenhouse.io/marqeta/jobs", "company": "Marqeta", "slug": "marqeta"},
]

WORKABLE_BOARDS = [
    {"url": "https://apply.workable.com/hayfin-capital-management", "company": "Hayfin", "slug": "hayfin-capital-management"},
    {"url": "https://apply.workable.com/castle-trust", "company": "Castle Trust Bank", "slug": "castle-trust"},
    {"url": "https://apply.workable.com/davy", "company": "Davy", "slug": "davy"},
    {"url": "https://apply.workable.com/homeprotect", "company": "Homeprotect", "slug": "homeprotect"},
    {"url": "https://apply.workable.com/insight-investment", "company": "Insight Investment", "slug": "insight-investment"},
    {"url": "https://apply.workable.com/moneyfarm", "company": "Moneyfarm", "slug": "moneyfarm"},
    {"url": "https://apply.workable.com/onyx-capital-group", "company": "Onyx Capital Group", "slug": "onyx-capital-group"},
    {"url": "https://apply.workable.com/pension-services-corporation-limited", "company": "Pension Insurance Corporation", "slug": "pension-services-corporation-limited"},
    {"url": "https://apply.workable.com/vortexa", "company": "Vortexa", "slug": "vortexa"},
    {"url": "https://apply.workable.com/zego", "company": "Zego", "slug": "zego"},
]

WORKDAY_BOARDS = [
    {"url": "https://athene.wd5.myworkdayjobs.com/en-US/Apollo_Careers", "company": "Apollo"},
    {"url": "https://lloyds.wd3.myworkdayjobs.com/en-US/Lloyds-of-London", "company": "Lloyd's"},
    {"url": "https://wd1.myworkdaysite.com/en-US/recruiting/wf/WellsFargoJobs", "company": "Wells Fargo"},
    {"url": "https://citi.wd5.myworkdayjobs.com/en-US/2", "company": "Citi"},
    {"url": "https://ukib.wd3.myworkdayjobs.com/en-US/UKIB", "company": "UK Infrastructure Bank"},
    {"url": "https://rbs.wd3.myworkdayjobs.com/en-US/RBS", "company": "NatWest Group"},
    {"url": "https://sedgwick.wd1.myworkdayjobs.com/en-US/Sedgwick", "company": "Sedgwick"},
    {"url": "https://bpinternational.wd3.myworkdayjobs.com/en-US/bpCareers", "company": "BP"},
    {"url": "https://tmhcc.wd108.myworkdayjobs.com/en-US/External", "company": "Tokio Marine HCC"},
    {"url": "https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers", "company": "Mastercard"},
    {"url": "https://mgpru.wd3.myworkdayjobs.com/en-US/mandgprudential", "company": "M&G Prudential"},
    {"url": "https://bbva.wd3.myworkdayjobs.com/en-US/BBVA", "company": "BBVA"},
    {"url": "https://ghr.wd1.myworkdayjobs.com/en-US/lateral-emea", "company": "Goldman Sachs"},
    {"url": "https://rabobank.wd3.myworkdayjobs.com/en-US/jobs", "company": "Rabobank"},
    {"url": "https://awg.wd3.myworkdayjobs.com/en-US/aw", "company": "Anglian Water"},
    {"url": "https://cibc.wd3.myworkdayjobs.com/en-US/search", "company": "CIBC"},
    {"url": "https://blackrock.wd1.myworkdayjobs.com/en-US/BlackRock_Professional", "company": "BlackRock"},
    {"url": "https://statestreet.wd1.myworkdayjobs.com/en-US/Global", "company": "State Street"},
]

ORACLE_BOARDS = [
    {"url": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001", "company": "JPMorgan Chase"},
    {"url": "https://dnn.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Nationwide", "company": "Nationwide"},
    {"url": "https://eoff.fa.em1.ukg.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001", "company": "Bank of England"},
    {"url": "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring", "company": "HDFC"},
    {"url": "https://eedu.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1003", "company": "Willis Towers Watson"},
    {"url": "https://ebxr.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1", "company": "DTCC"},
    {"url": "https://fa-euxc-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1", "company": "CITCO"},
    {"url": "https://fa-enor-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX", "company": "Phoenix"},
    {"url": "https://don.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1003", "company": "Euroclear"},
    {"url": "https://ebuu.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX", "company": "Westpac Banking"},
    {"url": "https://ejjl.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1", "company": "AustralianSuper"},
    {"url": "https://ejqi.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_1001", "company": "Danske Bank"},
    {"url": "https://ekbq.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2", "company": "Schroders"},
    {"url": "https://enlc.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001", "company": "IFM Investors"},
    {"url": "https://fa-emkq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX", "company": "Ascot"},
    {"url": "https://fa-eqai-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001", "company": "EFG"},
    {"url": "https://fa-errt-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001", "company": "SCOR"},
    {"url": "https://fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001", "company": "Computershare"},
    {"url": "https://fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/computersharecareers", "company": "Computershare"},
    {"url": "https://fa-ewgu-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001", "company": "Chubb"},
    {"url": "https://fa-exdv-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Careers", "company": "Westfield Specialty"},
    {"url": "https://hdfg.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX", "company": "Charles Stanley"},
    {"url": "https://hdow.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1003", "company": "BGC"},
    {"url": "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CampusHiring", "company": "Goldman Sachs"},
]

EIGHTFOLD_BOARDS = [
    {"url": "https://aexp.eightfold.ai/careers", "company": "American Express"},
    {"url": "https://morganstanley.eightfold.ai/careers", "company": "Morgan Stanley"},
    {"url": "https://bnymellon.eightfold.ai/careers", "company": "BNY Mellon"},
    {"url": "https://mlp.eightfold.ai/careers", "company": "Millennium"},
]

SUCCESSFACTORS_BOARDS = [
    {"url": "https://career2.successfactors.eu/career?company=royallondo", "company": "Royal London"},
    {"url": "https://career2.successfactors.eu/career?company=standardch", "company": "Standard Chartered"},
    {"url": "https://career8.successfactors.com/career?company=MoodysProd", "company": "Moody's"},
    {"url": "https://career2.successfactors.eu/career?company=mizuhoba01", "company": "Mizuho Financial Group"},
    {"url": "https://career2.successfactors.eu/career?company=tsbukprod", "company": "TSB Bank"},
    {"url": "https://career4.successfactors.com/career?company=natgridProd", "company": "National Grid"},
    {"url": "https://career5.successfactors.eu/career?company=banquepict", "company": "Pictet"},
]

ASHBY_BOARDS = [
    {"url": "https://jobs.ashbyhq.com/allica-bank", "company": "Allica Bank", "slug": "allica-bank"},
]

SMARTRECRUITERS_BOARDS = [
    {"url": "https://jobs.smartrecruiters.com/Together", "company": "Together", "slug": "Together"},
    {"url": "https://jobs.smartrecruiters.com/AJBell1", "company": "AJ Bell", "slug": "AJBell1"},
    {"url": "https://jobs.smartrecruiters.com/CPPInvestmentsInvestissementsRPC", "company": "CPP Investments", "slug": "CPPInvestmentsInvestissementsRPC"},
    {"url": "https://jobs.smartrecruiters.com/EvelynPartners", "company": "Evelyn Partners", "slug": "EvelynPartners"},
    {"url": "https://jobs.smartrecruiters.com/Octopus1", "company": "Octopus", "slug": "Octopus1"},
    {"url": "https://jobs.smartrecruiters.com/Visa", "company": "Visa", "slug": "Visa"},
    {"url": "https://jobs.smartrecruiters.com/Vitol", "company": "Vitol", "slug": "Vitol"},
    {"url": "https://jobs.smartrecruiters.com/esureGroup", "company": "Esure", "slug": "esureGroup"},
]

LEVER_BOARDS = [
    {"url": "https://jobs.lever.co/plaid", "company": "Plaid", "slug": "plaid"},
]

ICIMS_BOARDS = [
    {"url": "https://uk-stonex.icims.com/jobs", "company": "StoneX", "slug": "uk-stonex"},
    {"url": "https://ukjobs-ajg.icims.com/jobs", "company": "Gallagher", "slug": "ukjobs-ajg"},
    {"url": "https://careers-ameriprise.icims.com/jobs", "company": "Ameriprise Financial", "slug": "ameriprise"},
    {"url": "https://careers-principal.icims.com/jobs", "company": "Principal Financial Group", "slug": "principal"},
    {"url": "https://careers-unum.icims.com/jobs", "company": "Unum Group", "slug": "unum"},
    {"url": "https://careers-lfg.icims.com/jobs", "company": "Lincoln Financial", "slug": "lfg"},
    {"url": "https://careers-newyorklife.icims.com/jobs", "company": "New York Life", "slug": "newyorklife"},
    {"url": "https://careers-guardianlife.icims.com/jobs", "company": "Guardian Life", "slug": "guardianlife"},
    {"url": "https://careers-pacificlife.icims.com/jobs", "company": "Pacific Life", "slug": "pacificlife"},
    {"url": "https://careers-transamerica.icims.com/jobs", "company": "Transamerica", "slug": "transamerica"},
    {"url": "https://careers-protective.icims.com/jobs", "company": "Protective Life", "slug": "protective"},
    {"url": "https://careers-voya.icims.com/jobs", "company": "Voya Financial", "slug": "voya"},
    {"url": "https://careers-oneamerica.icims.com/jobs", "company": "OneAmerica", "slug": "oneamerica"},
]

_PLATFORM_LISTS = {
    "greenhouse": GREENHOUSE_BOARDS,
    "workable": WORKABLE_BOARDS,
    "workday": WORKDAY_BOARDS,
    "oracle": ORACLE_BOARDS,
    "eightfold": EIGHTFOLD_BOARDS,
    "successfactors": SUCCESSFACTORS_BOARDS,
    "ashby": ASHBY_BOARDS,
    "smartrecruiters": SMARTRECRUITERS_BOARDS,
    "lever": LEVER_BOARDS,
    "icims": ICIMS_BOARDS,
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalise_board_url(url: str | None) -> str | None:
    cleaned = _clean(url)
    if not cleaned:
        return None
    return cleaned.rstrip("/").lower()


def slug_to_company_label(slug: str | None) -> str | None:
    cleaned = _clean(slug)
    if not cleaned:
        return None
    return cleaned.replace("-", " ").replace("_", " ").strip().title()


def _build_company_by_url() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for rows in _PLATFORM_LISTS.values():
        for row in rows:
            url = normalise_board_url(row.get("url"))
            company = _clean(row.get("company"))
            if url and company:
                lookup[url] = company
    return lookup


def _build_company_by_slug() -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for adapter_name, rows in _PLATFORM_LISTS.items():
        for row in rows:
            slug = _clean(row.get("slug"))
            company = _clean(row.get("company"))
            if slug and company:
                lookup[(adapter_name, slug.lower())] = company
    return lookup


COMPANY_BY_URL = _build_company_by_url()
COMPANY_BY_SLUG = _build_company_by_slug()


def is_generic_company_name(company: str | None) -> bool:
    cleaned = _clean(company)
    if not cleaned:
        return True
    return cleaned.lower() in GENERIC_COMPANY_VALUES


def lookup_company(adapter_name: str, board_url: str | None = None, slug: str | None = None, explicit_company: str | None = None) -> str | None:
    normalized_url = normalise_board_url(board_url)
    if normalized_url and normalized_url in COMPANY_BY_URL:
        return COMPANY_BY_URL[normalized_url]
    cleaned_slug = _clean(slug)
    if cleaned_slug:
        from_slug = COMPANY_BY_SLUG.get((adapter_name, cleaned_slug.lower()))
        if from_slug:
            return from_slug
    cleaned_company = _clean(explicit_company)
    if cleaned_company and not is_generic_company_name(cleaned_company):
        return cleaned_company
    return slug_to_company_label(cleaned_slug) or cleaned_company
