from __future__ import annotations

import re
from hashlib import sha1

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from vacancysoft.db.models import EnrichedJob, RawJob, Source
from vacancysoft.enrichers.date_parser import parse_posted_date
from vacancysoft.enrichers.location_normaliser import (
    is_allowed_country, normalise_location,
    _US_STATE_CODES, _US_STATE_NAMES, _COUNTRY_ONLY,
)
from vacancysoft.enrichers.recruiter_filter import is_recruiter
from vacancysoft.classifiers.title_rules import is_relevant_title
from vacancysoft.source_registry.sector_classifier import detect_sector

_SENIORITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:chief|c-suite|cro|cfo|cto|coo|cio|ciso)\b", re.I), "c_suite"),
    (re.compile(r"\b(?:head of|global head|group head)\b", re.I), "head"),
    (re.compile(r"\b(?:managing director|md)\b", re.I), "managing_director"),
    (re.compile(r"\bdirector\b", re.I), "director"),
    (re.compile(r"\b(?:vice president|vp|svp|evp)\b", re.I), "vp"),
    (re.compile(r"\b(?:senior|sr\.?|lead|principal)\b", re.I), "senior"),
    (re.compile(r"\bmanager\b", re.I), "manager"),
    (re.compile(r"\b(?:associate|junior|jr\.?|trainee|graduate|intern|entry[ -]level)\b", re.I), "junior"),
    (re.compile(r"\b(?:analyst|officer|specialist|coordinator)\b", re.I), "mid"),
]


def _extract_seniority(title: str | None) -> str | None:
    if not title:
        return None
    for pattern, level in _SENIORITY_PATTERNS:
        if pattern.search(title):
            return level
    return None


def _extract_employer_from_payload(listing_payload: dict | None, provenance: dict | None = None) -> str | None:
    """Pull the real employer name from aggregator listing payloads."""
    if listing_payload:
        # Adzuna: {"company": {"display_name": "HAYS"}}
        company_obj = listing_payload.get("company")
        if isinstance(company_obj, dict):
            name = company_obj.get("display_name")
            if name:
                return str(name).strip()
        # Reed: {"employerName": "Fire Risk Prevention Agency LTD"}
        employer_name = listing_payload.get("employerName")
        if employer_name:
            return str(employer_name).strip()
        # Google Jobs: {"company_name": "Bank of America"}
        company_name = listing_payload.get("company_name")
        if company_name:
            return str(company_name).strip()
        # eFinancialCareers: {"companyName": "..."} or {"advertiserName": "..."}
        for key in ("companyName", "advertiserName"):
            val = listing_payload.get(key)
            if val:
                return str(val).strip()
        # eFinancialCareers nested: {"employer": {"name": "..."}}
        employer_obj = listing_payload.get("employer")
        if isinstance(employer_obj, dict):
            name = employer_obj.get("name")
            if name:
                return str(name).strip()
        # Generic fallback: plain "company" string
        if isinstance(company_obj, str) and company_obj.strip():
            return company_obj.strip()
    # Fallback: provenance blob (eFinancialCareers DOM parser stores company here)
    if provenance:
        company = provenance.get("company")
        if company and str(company).strip():
            return str(company).strip()
    return None


def _canonical_job_key(raw_job: RawJob, location: dict) -> str:
    basis = "|".join(
        [
            (raw_job.title_raw or "").strip().lower(),
            (location.get("city") or "").strip().lower(),
            (location.get("country") or "").strip().lower(),
            str(raw_job.source_id),
        ]
    )
    return sha1(basis.encode("utf-8")).hexdigest()


def _mark_filtered(session: Session, raw_job: RawJob, status: str, country: str | None = None) -> None:
    """Create or update a stub EnrichedJob so filtered jobs aren't re-queried."""
    existing = session.execute(
        select(EnrichedJob).where(EnrichedJob.raw_job_id == raw_job.id)
    ).scalar_one_or_none()
    if existing is None:
        stub = EnrichedJob(
            raw_job_id=raw_job.id,
            canonical_job_key=f"{status}_{raw_job.id}",
            location_country=country,
            detail_fetch_status=status,
        )
        session.add(stub)
        session.flush()
    elif existing.detail_fetch_status != status:
        existing.detail_fetch_status = status
        session.flush()


_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.I)


def _location_from_workday_path(listing_payload: dict | None) -> str | None:
    """Extract location from Workday externalPath when locationsText is 'N Locations'."""
    if not listing_payload:
        return None
    path = listing_payload.get("externalPath")
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "job":
        return parts[1].replace("-", " ")
    return None


# ── Well-known company HQ locations (fallback of last resort) ──────────
_COMPANY_HQ: dict[str, tuple[str, str]] = {
    "capital group": ("Los Angeles", "USA"),
    "sedgwick": ("Memphis", "USA"),
    "ryan specialty": ("Chicago", "USA"),
    "mfs": ("Boston", "USA"),
    "qbe insurance": ("Sydney", "Australia"),
    "qbe": ("London", "UK"),
    "arch insurance": ("New York", "USA"),
    "arch insurance (uk) limited": ("London", "UK"),
    "citi": ("New York", "USA"),
    "citigroup": ("New York", "USA"),
    "deutsche bank": ("Frankfurt", "Germany"),
    "bbva": ("Madrid", "Spain"),
    "nasdaq": ("New York", "USA"),
    "goldman sachs": ("New York", "USA"),
    "jpmorgan": ("New York", "USA"),
    "jpmorgan chase": ("New York", "USA"),
    "morgan stanley": ("New York", "USA"),
    "barclays": ("London", "UK"),
    "hsbc": ("London", "UK"),
    "ubs": ("Zurich", "Switzerland"),
    "credit suisse": ("Zurich", "Switzerland"),
    "bnp paribas": ("Paris", "France"),
    "societe generale": ("Paris", "France"),
    "allianz": ("Munich", "Germany"),
    "axa": ("Paris", "France"),
    "zurich insurance": ("Zurich", "Switzerland"),
    "aviva": ("London", "UK"),
    "lloyds banking group": ("London", "UK"),
    "standard chartered": ("London", "UK"),
    "blackrock": ("New York", "USA"),
    "fidelity": ("Boston", "USA"),
    "fidelity international": ("London", "UK"),
    "state street": ("Boston", "USA"),
    "northern trust": ("Chicago", "USA"),
    "bank of america": ("Charlotte", "USA"),
    "wells fargo": ("San Francisco", "USA"),
    "ameriprise": ("Minneapolis", "USA"),
    "apex": ("Bermuda", "USA"),
    "lseg": ("London", "UK"),
    "santander": ("Madrid", "Spain"),
    "chevron": ("San Ramon", "USA"),
    "rabobank": ("Utrecht", "Netherlands"),
    "td": ("Toronto", "Canada"),
    "old mutual": ("London", "UK"),
    "shell global": ("The Hague", "Netherlands"),
    "howden group holdings": ("London", "UK"),
    "everest": ("Warren", "USA"),
    "intrum": ("Stockholm", "Sweden"),
    "tokio marine hcc": ("Houston", "USA"),
    "ayvens": ("Paris", "France"),
    "markel": ("Richmond", "USA"),
    "mastercard": ("New York", "USA"),
    "starr companies": ("New York", "USA"),
    "hkex": ("Hong Kong", "Hong Kong"),
    "vanguard": ("Malvern", "USA"),
    "cigna": ("Bloomfield", "USA"),
    "commonwealth bank": ("Sydney", "Australia"),
    "viridien": ("London", "UK"),
    "pib group": ("London", "UK"),
    "tower research capital": ("New York", "USA"),
    "glenview capital management": ("New York", "USA"),
    "rothschild & co": ("London", "UK"),
    "mufg": ("Tokyo", "Japan"),
    "harbourvest": ("Boston", "USA"),
    "onyx capital group": ("London", "UK"),
    "man group": ("London", "UK"),
    "aztec group": ("Jersey", "Jersey"),
    "unum": ("Chattanooga", "USA"),
    "iberdrola": ("Bilbao", "Spain"),
    "bp": ("London", "UK"),
    # Companies from Oracle/Greenhouse N/A results
    "hdfc": ("Mumbai", "India"),
    "phoenix": ("Edinburgh", "UK"),
    "computershare": ("Melbourne", "Australia"),
    "citco": ("Luxembourg City", "Luxembourg"),
    "westfield specialty": ("New York", "USA"),
    "scor": ("Paris", "France"),
    "danske bank": ("Copenhagen", "Denmark"),
    "nationwide": ("Swindon", "UK"),
    "bank of england": ("London", "UK"),
    "dtcc": ("New York", "USA"),
    "efg": ("Zurich", "Switzerland"),
    "ascot": ("London", "UK"),
    "australiansuper": ("Melbourne", "Australia"),
    "bgc": ("New York", "USA"),
    "euroclear": ("Brussels", "Belgium"),
    "schroders": ("London", "UK"),
    "eni": ("Rome", "Italy"),
    "ifm investors": ("Melbourne", "Australia"),
    "westpac banking": ("Sydney", "Australia"),
    "charles stanley": ("London", "UK"),
    "b. riley": ("Los Angeles", "USA"),
    "affinity water": ("Hatfield", "UK"),
    "s-rm": ("London", "UK"),
    # Greenhouse companies
    "stripe": ("San Francisco", "USA"),
    "coinbase": ("San Francisco", "USA"),
    "brex": ("San Francisco", "USA"),
    "robinhood": ("Menlo Park", "USA"),
    "interactive brokers": ("Greenwich", "USA"),
    "drw holdings": ("Chicago", "USA"),
    "chime": ("San Francisco", "USA"),
    "carta": ("San Francisco", "USA"),
    "monzo": ("London", "UK"),
    "marqeta": ("Oakland", "USA"),
    "maven": ("London", "UK"),
    "flow traders": ("Amsterdam", "Netherlands"),
    "policy expert": ("London", "UK"),
    "lhv": ("Tallinn", "Estonia"),
    "rothesay": ("London", "UK"),
    "winton": ("London", "UK"),
    "bluecrest capital management": ("London", "UK"),
    "pharo management": ("London", "UK"),
    "srm": ("London", "UK"),
    "chubb": ("New York", "USA"),
    "willis towers watson": ("London", "UK"),
    "allstate": ("Northbrook", "USA"),
    "amundi asset management": ("Paris", "France"),
    "bacb": ("London", "UK"),
    # Generic browser board companies
    "7im": ("London", "UK"),
    "aqr capital management (europe) llp": ("London", "UK"),
    "arag": ("Bristol", "UK"),
    "adyen": ("Amsterdam", "Netherlands"),
    "ageas": ("Stoke-on-Trent", "UK"),
    "aldermore bank": ("London", "UK"),
    "allied irish banks": ("Dublin", "Ireland"),
    "ally invest": ("Charlotte", "USA"),
    "alphadyne": ("New York", "USA"),
    "ambac assurance uk limited": ("London", "UK"),
    "aon": ("London", "UK"),
    "arbuthnot latham": ("London", "UK"),
    "assurant": ("Atlanta", "USA"),
    "balyasny": ("Chicago", "USA"),
    "benefact group": ("London", "UK"),
    "berenberg": ("Hamburg", "Germany"),
    "betterment": ("New York", "USA"),
    "bloomberg": ("New York", "USA"),
    "broadstone": ("London", "UK"),
    "bunge": ("St. Louis", "USA"),
    "cfm": ("Paris", "France"),
    "cofco international": ("Geneva", "Switzerland"),
    "carlyle": ("Washington DC", "USA"),
    "cathay bank": ("Los Angeles", "USA"),
    "co-operative bank": ("Manchester", "UK"),
    "cumberland building society": ("Carlisle", "UK"),
    "development bank of wales": ("Cardiff", "UK"),
    "drax group": ("Selby", "UK"),
    "ebrd": ("London", "UK"),
    "ecotricity": ("Stroud", "UK"),
    "enstar group": ("Windsor", "UK"),
    "exxonmobil": ("Houston", "USA"),
    "fis": ("Jacksonville", "USA"),
    "faraday": ("London", "UK"),
    "first central insurance & technology group": ("Haywards Heath", "UK"),
    "gcm grosvenor": ("Chicago", "USA"),
    "gic": ("Singapore", "Singapore"),
    "glencore": ("Baar", "Switzerland"),
    "hdi global specialty": ("London", "UK"),
    "imc": ("Amsterdam", "Netherlands"),
    "ing": ("Amsterdam", "Netherlands"),
    "insight partners": ("New York", "USA"),
    "intesa sanpaolo": ("Turin", "Italy"),
    "irish life group": ("Dublin", "Ireland"),
    "isio": ("London", "UK"),
    "jtc group": ("Jersey", "Jersey"),
    "jane street": ("New York", "USA"),
    "janus henderson": ("London", "UK"),
    "jump trading": ("Chicago", "USA"),
    "klarna": ("Stockholm", "Sweden"),
    "koch industries": ("Wichita", "USA"),
    "lv=": ("Bournemouth", "UK"),
    "lightsource bp": ("London", "UK"),
    "linde": ("Woking", "UK"),
    "lockton": ("London", "UK"),
    "loomis sayles": ("Boston", "USA"),
    "msci": ("New York", "USA"),
    "macquarie": ("Sydney", "Australia"),
    "motability operations": ("London", "UK"),
    "nomura": ("London", "UK"),
    "octopus energy": ("London", "UK"),
    "paragon banking group": ("Solihull", "UK"),
    "principal asset management": ("Des Moines", "USA"),
    "principality building society": ("Cardiff", "UK"),
    "qube research & technologies": ("London", "UK"),
    "rostella": ("London", "UK"),
    "s&p global": ("New York", "USA"),
    "scotiabank": ("Toronto", "Canada"),
    "scottish friendly": ("Glasgow", "UK"),
    "secure trust bank": ("Solihull", "UK"),
    "shawbrook": ("London", "UK"),
    "shell": ("London", "UK"),
    "simplyhealth": ("Andover", "UK"),
    "siriuspoint": ("Hamilton", "Bermuda"),
    "so energy": ("London", "UK"),
    "st james's place": ("Cirencester", "UK"),
    "tesco insurance": ("Glasgow", "UK"),
    "the ardonagh group": ("London", "UK"),
    "the nottingham": ("Nottingham", "UK"),
    "the openwork partnership": ("Swindon", "UK"),
    "titan": ("New York", "USA"),
    "two sigma": ("New York", "USA"),
    "vitality": ("Bournemouth", "UK"),
    "xtx markets": ("London", "UK"),
    "motonovo finance": ("Cardiff", "UK"),
    # Added from N/A audit - companies with no location data
    "unicredit": ("Milan", "Italy"),
    "columbia threadneedle investments": ("London", "UK"),
    "liberty specialty markets": ("London", "UK"),
    "squarepoint": ("London", "UK"),
    "squarepoint capital": ("London", "UK"),
    "d. e. shaw": ("New York", "USA"),
    "de shaw": ("New York", "USA"),
    "hamilton": ("Bermuda", "USA"),
    "mizuho financial group": ("London", "UK"),
    "lord abbett": ("Jersey City", "USA"),
    "berkshire hathaway international insurance limited": ("London", "UK"),
    "standard bank": ("London", "UK"),
    "apex fintech solutions": ("Dallas", "USA"),
    "miller": ("London", "UK"),
    "atrium": ("London", "UK"),
    "dll": ("Eindhoven", "Netherlands"),
    "bny": ("New York", "USA"),
    "bny mellon": ("New York", "USA"),
    "abu dhabi commercial bank": ("Abu Dhabi", "UAE"),
    "adcb": ("Abu Dhabi", "UAE"),
    "bmo": ("Toronto", "Canada"),
    "bmo capital markets": ("Toronto", "Canada"),
    "bank of london": ("London", "UK"),
    "close brothers": ("London", "UK"),
    "citadel": ("Chicago", "USA"),
    "citadel securities": ("Chicago", "USA"),
    "royal london": ("London", "UK"),
    "moody's": ("New York", "USA"),
    "gfi group": ("New York", "USA"),
    "fmx": ("New York", "USA"),
    "blue owl": ("New York", "USA"),
    "blue owl capital": ("New York", "USA"),
    "pictet": ("Geneva", "Switzerland"),
    "absa": ("Johannesburg", "South Africa"),
    "right mortgage": ("Wolverhampton", "UK"),
    "evolution money": ("Blackburn", "UK"),
    "bgl group": ("Peterborough", "UK"),
    "walker crips": ("London", "UK"),
    "utmost international": ("Douglas", "UK"),
    "trafigura": ("Geneva", "Switzerland"),
    "tastytrade": ("Chicago", "USA"),
    "pagaya": ("New York", "USA"),
    "marsden building society": ("Nelson", "UK"),
    "leek building society": ("Leek", "UK"),
    "hanley economic building society": ("Hanley", "UK"),
    "fidelis": ("London", "UK"),
    "cambridge building society": ("Cambridge", "UK"),
    "aventum": ("London", "UK"),
    "alan boswell group": ("Norwich", "UK"),
    "berkley": ("Greenwich", "USA"),
}

# US state names/codes embedded in location strings like "Remote - Wisconsin", "Telecommuter NY"
_US_STATE_RE = re.compile(
    r"(?:^|\s|[-–/])("
    + "|".join(re.escape(s) for s in sorted(_US_STATE_CODES, key=len, reverse=True))
    + r")(?:\s|$|[-–/,])",
    re.IGNORECASE,
)

_US_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in sorted(_US_STATE_NAMES.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _enhanced_location_resolve(
    location_raw: str | None,
    title: str | None,
    employer: str | None,
) -> dict | None:
    """Try harder to resolve location when normalise_location returns no country.

    Strategies:
    1. Extract US state code from location string (e.g. "Telecommuter NY", "Home Base CA")
    2. Extract US state name from location string (e.g. "Remote - Wisconsin", "California-Remote")
    3. Extract city from job title (e.g. "Consultant - London", "VP Risk - New York")
    4. Use company HQ as last resort
    """
    loc_str = (location_raw or "").strip()
    title_str = (title or "").strip()
    employer_str = (employer or "").strip()

    # 1. US state code in location: "Telecommuter NY", "Home Base CA", "USA CA LA South Flower"
    m = _US_STATE_RE.search(loc_str)
    if m:
        code = m.group(1).upper()
        if code in _US_STATE_CODES:
            return {"city": "Remote" if "remote" in loc_str.lower() or "telecommut" in loc_str.lower() else loc_str, "country": "USA", "confidence": 0.75}

    # 2. US state name in location: "Remote - Wisconsin", "California-Remote", "Massachusetts Remote"
    m = _US_STATE_NAME_RE.search(loc_str)
    if m:
        return {"city": "Remote" if "remote" in loc_str.lower() or "telecommut" in loc_str.lower() else m.group(1).title(), "country": "USA", "confidence": 0.75}

    # 3. Try to resolve from title (city names after dash/comma)
    # e.g. "XC Wealth Consultant - London", "VP Credit Risk, New York"
    if title_str:
        title_loc = normalise_location(title_str.split(" - ")[-1].strip()) if " - " in title_str else None
        if title_loc and title_loc.get("country") and title_loc.get("confidence", 0) >= 0.5:
            return title_loc
        # Also try after comma
        if "," in title_str:
            title_loc = normalise_location(title_str.split(",")[-1].strip())
            if title_loc and title_loc.get("country") and title_loc.get("confidence", 0) >= 0.5:
                return title_loc

    # 4. "Regional" with city in title
    if loc_str.lower() == "regional" and title_str:
        # Check title for any location hint
        title_loc = normalise_location(title_str)
        if title_loc and title_loc.get("country") and title_loc.get("confidence", 0) >= 0.5:
            return title_loc

    # 5. Company HQ fallback
    if employer_str:
        hq = _COMPANY_HQ.get(employer_str.strip().lower())
        if hq:
            return {"city": hq[0], "country": hq[1], "confidence": 0.5}

    return None


def persist_enrichment_for_raw_job(
    session: Session,
    raw_job: RawJob,
    *,
    skip_filters: bool = False,
) -> EnrichedJob | None:
    """Enrich a raw job. Returns None if filtered out (wrong country or recruiter).

    ``skip_filters=True`` bypasses the three allow/reject gates
    (allowed-country, recruiter-detection, title-relevance). Used by the
    text-paste endpoint where the operator has explicitly chosen the
    advert and the filters would only cause surprising 422s. Location
    normalisation still runs — we just don't reject based on its
    result.
    """
    location_raw = raw_job.location_raw
    # For "N Locations" entries, try to extract from Workday URL path
    if location_raw and _MULTI_LOC_RE.match(location_raw.strip()):
        from_path = _location_from_workday_path(raw_job.listing_payload)
        if from_path:
            location_raw = from_path
    location = normalise_location(location_raw)

    # If standard normaliser couldn't resolve country, try enhanced resolution
    if not location.get("country"):
        source = session.execute(
            select(Source).where(Source.id == raw_job.source_id)
        ).scalar_one_or_none()
        employer_hint = (
            _extract_employer_from_payload(raw_job.listing_payload, raw_job.provenance_blob)
            or (source.employer_name if source else None)
        )
        enhanced = _enhanced_location_resolve(location_raw, raw_job.title_raw, employer_hint)
        if enhanced and enhanced.get("country"):
            location["city"] = enhanced.get("city") or location.get("city")
            location["country"] = enhanced["country"]
            location["confidence"] = enhanced.get("confidence", 0.5)

    if not skip_filters:
        if not is_allowed_country(location.get("country")):
            _mark_filtered(session, raw_job, "geo_filtered", location.get("country"))
            return None

        # Check employer from aggregator payload / provenance + source table
        extracted_employer = _extract_employer_from_payload(raw_job.listing_payload, raw_job.provenance_blob)
        if not extracted_employer:
            source = session.execute(
                select(Source).where(Source.id == raw_job.source_id)
            ).scalar_one_or_none()
            employer_to_check = source.employer_name if source else None
        else:
            employer_to_check = extracted_employer
        if is_recruiter(employer_to_check):
            _mark_filtered(session, raw_job, "agency_filtered", location.get("country"))
            return None

        # Title must match at least one taxonomy keyword
        if not is_relevant_title(raw_job.title_raw):
            _mark_filtered(session, raw_job, "title_filtered", location.get("country"))
            return None
    else:
        # skip_filters path: still resolve the employer once so the
        # values dict below gets the real team name (not "(Manual paste)").
        extracted_employer = _extract_employer_from_payload(
            raw_job.listing_payload, raw_job.provenance_blob
        )

    posted_at = parse_posted_date(raw_job.posted_at_raw)
    title = raw_job.title_raw
    title_normalised = title.strip().lower() if title else None
    canonical_job_key = _canonical_job_key(raw_job, location)
    seniority = _extract_seniority(title)

    # Resolve the employer this lead actually represents:
    # 1. payload-extracted employer (winning for aggregator leads), else
    # 2. the source's employer_name (winning for direct leads).
    # Then classify the sector once at enrichment time so the resulting
    # EnrichedJob carries `employer_sector` independent of how it was
    # scraped. Aggregator-fed Goldman jobs end up with
    # employer_sector='investment_bank', not 'aggregator'.
    resolved_employer = extracted_employer
    if not resolved_employer:
        src_for_sector = session.execute(
            select(Source).where(Source.id == raw_job.source_id)
        ).scalar_one_or_none()
        resolved_employer = src_for_sector.employer_name if src_for_sector else None
    employer_sector = detect_sector(
        resolved_employer or "",
        # Pass empty adapter_name so aggregator override doesn't clobber
        # the per-employer classification at enrichment time. The lead
        # belongs to the underlying employer, not to the source's adapter.
        "",
        "",
    )

    existing = session.execute(
        select(EnrichedJob).where(EnrichedJob.raw_job_id == raw_job.id)
    ).scalar_one_or_none()

    values = {
        "raw_job_id": raw_job.id,
        "canonical_job_key": canonical_job_key,
        "title": title,
        "title_normalised": title_normalised,
        "location_text": raw_job.location_raw,
        "location_country": location.get("country"),
        "location_city": location.get("city"),
        "location_region": location.get("region"),
        "location_type": None,
        "posted_at": posted_at,
        "freshness_bucket": "recent" if posted_at else "unknown",
        "description_text": raw_job.description_raw,
        "team": extracted_employer,
        "employment_type": None,
        "seniority_hint": seniority,
        "business_area_hint": None,
        "employer_sector": employer_sector,
        "detail_fetch_status": "enriched",
        "enrichment_confidence": max(location.get("confidence", 0.0), raw_job.extraction_confidence),
        "completeness_score": raw_job.completeness_score,
        "provenance_blob": {
            "raw_job_id": raw_job.id,
            "mode": "enrichment_v1",
        },
    }

    if existing is None:
        enriched = EnrichedJob(**values)
        session.add(enriched)
        session.flush()
        return enriched

    for key, value in values.items():
        setattr(existing, key, value)
    session.flush()
    return existing


def enrich_raw_jobs(
    session: Session,
    limit: int | None = None,
    adapter_name: str | None = None,
) -> int:
    """Enrich every RawJob that hasn't been enriched yet.

    Uses NOT EXISTS (not NOT IN) so Postgres can leverage the unique
    index on enriched_jobs.raw_job_id as an anti-semi-join; the old
    NOT IN (subquery) pattern degenerated into a sequential scan over
    all raw_jobs on tables this size (2026-04-20 investigation: 140k
    rows, query took minutes and caused a visible pipeline stall).

    When `adapter_name` is set, the scan is further narrowed to RawJobs
    whose Source has that adapter — matches operator expectation for
    `prospero pipeline run --adapter <x>` and keeps each round cheap.
    """
    stmt = (
        select(RawJob)
        .where(
            ~exists().where(EnrichedJob.raw_job_id == RawJob.id),
            # Operator-marked "dead" rows (via the Sources page "Dead job"
            # admin button — see api/routes/leads.py::delete_lead) set
            # is_deleted_at_source=true so they're skipped here and don't
            # re-enrich on the next pipeline pass. The column existed
            # historically for "the ATS removed this posting" but nothing
            # was reading it; operator-dead-flag is the first live use.
            RawJob.is_deleted_at_source.is_(False),
        )
    )
    if adapter_name is not None:
        stmt = (
            stmt.join(Source, RawJob.source_id == Source.id)
            .where(Source.adapter_name == adapter_name)
        )
    stmt = stmt.order_by(RawJob.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    raw_jobs = list(session.execute(stmt).scalars())

    count = 0
    for raw_job in raw_jobs:
        result = persist_enrichment_for_raw_job(session, raw_job)
        if result is not None:
            count += 1

    session.commit()
    return count
