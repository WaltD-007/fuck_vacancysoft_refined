from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_RULES_YAML_PATH = Path(__file__).resolve().parents[3] / "configs" / "location_rules.yaml"

# ══════════════════════════════════════════════════════════════════════════
# LOAD RULES: prefer YAML config, fall back to hardcoded defaults
# ══════════════════════════════════════════════════════════════════════════


def _load_rules_from_yaml() -> tuple[list[tuple[str, str, str]], dict[str, str], set[str]] | None:
    """Try to load location rules from YAML config. Returns None on failure."""
    try:
        if not _RULES_YAML_PATH.exists():
            return None
        with open(_RULES_YAML_PATH) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None

        rules = [
            (str(r["keyword"]), str(r["city"]), str(r["country"]))
            for r in data.get("city_rules", [])
            if isinstance(r, dict) and "keyword" in r and "city" in r and "country" in r
        ]
        country_only = {
            str(k): str(v)
            for k, v in (data.get("country_only") or {}).items()
        }
        allowed = set(str(c) for c in (data.get("allowed_countries") or []))

        if rules:
            logger.info("Loaded %d location rules from %s", len(rules), _RULES_YAML_PATH)
            return rules, country_only, allowed
    except Exception as exc:
        logger.warning("Failed to load location rules from YAML: %s. Using defaults.", exc)
    return None


_yaml_result = _load_rules_from_yaml()

_DEFAULT_RULES: list[tuple[str, str, str]] = [
    # ── UK ───────────────────────────────────────────────────────────────
    ("cheadle",             "Cheadle",           "UK"),
    ("glasgow",             "Glasgow",           "UK"),
    ("edinburgh",           "Edinburgh",         "UK"),
    ("belfast",             "Belfast",           "UK"),
    ("cardiff",             "Cardiff",           "UK"),
    ("sheffield",           "Sheffield",         "UK"),
    ("manchester",          "Manchester",        "UK"),
    ("bristol",             "Bristol",           "UK"),
    ("birmingham",          "Birmingham",        "UK"),
    ("peterborough",        "Peterborough",      "UK"),
    ("warwick",             "Warwick",           "UK"),
    ("brighton",            "Brighton",          "UK"),
    ("chelmsford",          "Chelmsford",        "UK"),
    ("reigate",             "Reigate",           "UK"),
    ("skipton",             "Skipton",           "UK"),
    ("solihull",            "Solihull",          "UK"),
    ("stafford",            "Stafford",          "UK"),
    ("sunbury",             "Sunbury",           "UK"),
    ("bridgend",            "Bridgend",          "UK"),
    ("lincoln",             "Lincoln",           "UK"),
    ("leeds",               "Leeds",             "UK"),
    ("city of london",      "London",            "UK"),
    ("lloyd's uk",          "London",            "UK"),
    ("lloyd's belgium",     "Brussels",          "Belgium"),
    ("jersey / london",     "London",            "UK"),
    ("london",              "London",            "UK"),
    ("st helier",           "St Helier",         "Jersey"),
    ("jersey city",         "Jersey City",       "USA"),
    ("new jersey",          "New Jersey",        "USA"),
    ("jersey",              "Jersey",            "Jersey"),
    ("guernsey",            "Guernsey",          "Guernsey"),
    ("st peter port",       "St Peter Port",     "Guernsey"),
    ("leicester",           "Leicester",         "UK"),
    ("nottingham",          "Nottingham",        "UK"),
    ("liverpool",           "Liverpool",         "UK"),
    ("newcastle",           "Newcastle",         "UK"),
    ("coventry",            "Coventry",          "UK"),
    ("reading",             "Reading",           "UK"),
    ("oxford",              "Oxford",            "UK"),
    ("cambridge",           "Cambridge",         "UK"),
    ("bath",                "Bath",              "UK"),
    ("exeter",              "Exeter",            "UK"),
    ("norwich",             "Norwich",           "UK"),
    ("ipswich",             "Ipswich",           "UK"),
    ("aberdeen",            "Aberdeen",          "UK"),
    ("dundee",              "Dundee",            "UK"),
    ("inverness",           "Inverness",         "UK"),
    ("swansea",             "Swansea",           "UK"),
    ("newport",             "Newport",           "UK"),
    ("guildford",           "Guildford",         "UK"),
    ("croydon",             "Croydon",           "UK"),
    ("watford",             "Watford",           "UK"),
    ("luton",               "Luton",             "UK"),
    ("crawley",             "Crawley",           "UK"),
    ("basildon",            "Basildon",          "UK"),
    ("st albans",           "St Albans",         "UK"),
    ("farnborough",         "Farnborough",       "UK"),
    ("woking",              "Woking",            "UK"),
    ("bournemouth",         "Bournemouth",       "UK"),
    ("swindon",             "Swindon",           "UK"),
    ("southampton",         "Southampton",       "UK"),
    ("borehamwood",         "Borehamwood",       "UK"),
    ("wimbledon",           "Wimbledon",         "UK"),
    ("chester",             "Chester",           "UK"),
    ("chatham",             "Chatham",           "UK"),
    ("dorking",             "Dorking",           "UK"),
    ("staines",             "Staines",           "UK"),
    ("sunderland",          "Sunderland",        "UK"),
    ("plymouth",            "Plymouth",          "UK"),
    ("derby",               "Derby",             "UK"),
    ("harrogate",           "Harrogate",         "UK"),
    ("stockport",           "Stockport",         "UK"),
    ("bolton",              "Bolton",            "UK"),
    ("preston",             "Preston",           "UK"),
    ("blackburn",           "Blackburn",         "UK"),
    ("halifax",             "Halifax",           "UK"),
    ("huddersfield",        "Huddersfield",      "UK"),
    ("doncaster",           "Doncaster",         "UK"),
    ("wakefield",           "Wakefield",         "UK"),
    ("colchester",          "Colchester",        "UK"),
    ("maidstone",           "Maidstone",         "UK"),
    ("tunbridge wells",     "Tunbridge Wells",   "UK"),
    ("sevenoaks",           "Sevenoaks",         "UK"),
    ("epsom",               "Epsom",             "UK"),
    ("kingston upon thames", "Kingston",          "UK"),
    ("canary wharf",        "London",            "UK"),
    ("haywards heath",      "Haywards Heath",    "UK"),
    ("barnet",              "Barnet",            "UK"),
    ("bellshill",           "Bellshill",         "UK"),
    ("bexhill",             "Bexhill",           "UK"),
    ("bradford",            "Bradford",          "UK"),
    ("dagenham",            "Dagenham",          "UK"),
    ("lichfield",           "Lichfield",         "UK"),
    ("markham",             "Markham",           "Canada"),
    ("milton keynes",       "Milton Keynes",     "UK"),
    ("pontefract",          "Pontefract",        "UK"),
    ("rochdale",            "Rochdale",          "UK"),
    ("stirling",            "Stirling",          "UK"),
    ("uxbridge",            "Uxbridge",          "UK"),
    ("walkden",             "Walkden",           "UK"),
    ("walsall",             "Walsall",           "UK"),
    ("wilmslow",            "Wilmslow",          "UK"),
    ("worthing",            "Worthing",          "UK"),
    ("yeadon",              "Yeadon",            "UK"),
    ("fareham",             "Fareham",           "UK"),
    ("maidenhead",          "Maidenhead",        "UK"),
    ("surrey",              "Surrey",            "UK"),
    ("west midlands",       "West Midlands",     "UK"),
    ("hampshire",           "Hampshire",         "UK"),
    ("west sussex",         "West Sussex",       "UK"),
    ("bedford",             "Bedford",           "USA"),
    ("foster city",         "Foster City",       "USA"),
    ("troy",                "Troy",              "USA"),
    ("sofia",               "Sofia",             "Bulgaria"),
    ("gibraltar",           "Gibraltar",         "UK"),

    # ── USA (must be before UK "york" entry) ────────────────────────────
    ("new york",            "New York",          "USA"),
    ("1 columbus circle",   "New York",          "USA"),
    ("300 vesey",           "New York",          "USA"),
    ("1185 ave of america", "New York",          "USA"),
    ("ny-new york",         "New York",          "USA"),
    (" nyc",                "New York",          "USA"),
    ("ny,",                 "New York",          "USA"),
    ("ny ",                 "New York",          "USA"),
    ("york",                "York",              "UK"),
    ("newark",              "Newark",            "USA"),
    ("chicago",             "Chicago",           "USA"),
    ("20 s. wacker",        "Chicago",           "USA"),
    ("boston",               "Boston",            "USA"),
    ("atlanta",             "Atlanta",           "USA"),
    ("charlotte",           "Charlotte",         "USA"),
    ("houston",             "Houston",           "USA"),
    ("dallas",              "Dallas",            "USA"),
    ("allen, texas",        "Allen",             "USA"),
    ("usa-allen",           "Allen",             "USA"),
    ("st. louis",           "St. Louis",         "USA"),
    ("usa-st. louis",       "St. Louis",         "USA"),
    ("philadelphia",        "Philadelphia",      "USA"),
    ("los angeles",         "Los Angeles",       "USA"),
    ("san francisco",       "San Francisco",     "USA"),
    ("san antonio",         "San Antonio",       "USA"),
    ("newport beach",       "Newport Beach",     "USA"),
    ("austin",              "Austin",            "USA"),
    ("tampa",               "Tampa",             "USA"),
    ("miami",               "Miami",             "USA"),
    ("irving",              "Irving",            "USA"),
    ("washington",          "Washington DC",     "USA"),
    ("denver",              "Denver",            "USA"),
    ("minneapolis",         "Minneapolis",       "USA"),
    ("jacksonville",        "Jacksonville",      "USA"),
    ("chattanooga",         "Chattanooga",       "USA"),
    ("morristown",          "Morristown",        "USA"),
    ("wilmington",          "Wilmington",        "USA"),
    ("baltimore",           "Baltimore",         "USA"),
    ("merrimack",           "Merrimack",         "USA"),
    ("smithfield",          "Smithfield",        "USA"),
    ("o'fallon",            "O'Fallon",          "USA"),
    ("ofallon",             "O'Fallon",          "USA"),
    ("ft. lauderdale",      "Fort Lauderdale",   "USA"),
    ("fort lauderdale",     "Fort Lauderdale",   "USA"),
    ("purchase, new york",  "Purchase",          "USA"),
    ("getzville",           "Getzville",         "USA"),
    ("oaks",                "Oaks",              "USA"),
    ("stamford",            "Stamford",          "USA"),
    ("greenwich",           "Greenwich",         "USA"),
    ("el segundo",          "El Segundo",        "USA"),
    ("carmel",              "Carmel",            "USA"),
    ("san jose",            "San Jose",          "USA"),
    ("detroit",             "Detroit",           "USA"),
    ("pittsburgh",          "Pittsburgh",        "USA"),
    ("salt lake city",      "Salt Lake City",    "USA"),
    ("portland",            "Portland",          "USA"),
    ("raleigh",             "Raleigh",           "USA"),
    ("nashville",           "Nashville",         "USA"),
    ("orlando",             "Orlando",           "USA"),
    ("phoenix",             "Phoenix",           "USA"),
    ("san diego",           "San Diego",         "USA"),
    ("columbus",            "Columbus",          "USA"),
    ("indianapolis",        "Indianapolis",      "USA"),
    ("milwaukee",           "Milwaukee",         "USA"),
    ("des moines",          "Des Moines",        "USA"),
    ("hoboken",             "Hoboken",           "USA"),
    ("hartford",            "Hartford",          "USA"),
    ("st louis",            "St. Louis",         "USA"),
    ("kansas city",         "Kansas City",       "USA"),
    ("richmond",            "Richmond",          "USA"),
    ("omaha",               "Omaha",             "USA"),
    ("plano",               "Plano",             "USA"),
    ("arlington",           "Arlington",         "USA"),
    ("sacramento",          "Sacramento",        "USA"),
    ("cleveland",           "Cleveland",         "USA"),
    ("cincinnati",          "Cincinnati",        "USA"),
    ("memphis",             "Memphis",           "USA"),
    ("louisville",          "Louisville",        "USA"),
    ("oklahoma city",       "Oklahoma City",     "USA"),
    ("tucson",              "Tucson",            "USA"),
    ("las vegas",           "Las Vegas",         "USA"),
    ("reno",                "Reno",              "USA"),
    ("albuquerque",         "Albuquerque",       "USA"),
    ("new haven",           "New Haven",         "USA"),
    ("ann arbor",           "Ann Arbor",         "USA"),
    ("boulder",             "Boulder",           "USA"),
    ("durham",              "Durham",            "USA"),
    ("chapel hill",         "Chapel Hill",       "USA"),
    ("madison",             "Madison",           "USA"),
    ("irvine",              "Irvine",            "USA"),
    ("pasadena",            "Pasadena",          "USA"),
    ("santa monica",        "Santa Monica",      "USA"),
    ("palo alto",           "Palo Alto",         "USA"),
    ("menlo park",          "Menlo Park",        "USA"),
    ("mountain view",       "Mountain View",     "USA"),
    ("sunnyvale",           "Sunnyvale",         "USA"),
    ("cupertino",           "Cupertino",         "USA"),
    ("redmond",             "Redmond",           "USA"),
    ("seattle",             "Seattle",           "USA"),
    # US cities that also exist in other countries (need constrained lookup)
    ("birmingham",          "Birmingham",        "USA"),
    ("cambridge",           "Cambridge",         "USA"),
    ("windsor",             "Windsor",           "USA"),
    ("kingston",            "Kingston",          "USA"),
    ("victoria",            "Victoria",          "USA"),
    ("hamilton",            "Hamilton",          "USA"),
    ("remote - usa",        "Remote",            "USA"),
    ("united states-remote","Remote",            "USA"),
    ("united states work at home", "Remote",     "USA"),
    ("flexible - us",       "Remote",            "USA"),

    # ── Canada ───────────────────────────────────────────────────────────
    ("mississauga",         "Mississauga",       "Canada"),
    ("toronto",             "Toronto",           "Canada"),
    ("montreal",            "Montreal",          "Canada"),
    ("vancouver",           "Vancouver",         "Canada"),
    ("calgary",             "Calgary",           "Canada"),
    ("ottawa",              "Ottawa",            "Canada"),
    ("winnipeg",            "Winnipeg",          "Canada"),
    ("edmonton",            "Edmonton",          "Canada"),
    ("london",              "London",            "Canada"),
    ("hamilton",            "Hamilton",          "Canada"),
    ("kingston",            "Kingston",          "Canada"),
    ("victoria",            "Victoria",          "Canada"),
    ("windsor",             "Windsor",           "Canada"),
    ("cambridge",           "Cambridge",         "Canada"),
    ("canada - qc",         "Remote",            "Canada"),

    # ── Ireland ──────────────────────────────────────────────────────────
    ("letterkenny",         "Letterkenny",       "Ireland"),
    ("loughlinstown",       "Loughlinstown",     "Ireland"),
    ("limerick",            "Limerick",          "Ireland"),
    ("dublin",              "Dublin",            "Ireland"),
    ("cork",                "Cork",              "Ireland"),
    ("galway",              "Galway",            "Ireland"),

    # ── France ───────────────────────────────────────────────────────────
    ("massy",               "Massy",             "France"),
    ("paris",               "Paris",             "France"),
    ("lyon",                "Lyon",              "France"),
    ("marseille",           "Marseille",         "France"),
    ("nice",                "Nice",              "France"),
    ("toulouse",            "Toulouse",          "France"),

    # ── Germany ──────────────────────────────────────────────────────────
    ("frankfurt",           "Frankfurt",         "Germany"),
    ("kronberg",            "Frankfurt",         "Germany"),
    ("munich",              "Munich",            "Germany"),
    ("berlin",              "Berlin",            "Germany"),
    ("hamburg",             "Hamburg",           "Germany"),
    ("dusseldorf",          "Düsseldorf",        "Germany"),
    ("düsseldorf",          "Düsseldorf",        "Germany"),
    ("cologne",             "Cologne",           "Germany"),

    # ── Netherlands ──────────────────────────────────────────────────────
    ("amsterdam",           "Amsterdam",         "Netherlands"),
    ("rotterdam",           "Rotterdam",         "Netherlands"),
    ("utrecht",             "Utrecht",           "Netherlands"),
    ("the hague",           "The Hague",         "Netherlands"),
    ("eindhoven",           "Eindhoven",         "Netherlands"),

    # ── Belgium ──────────────────────────────────────────────────────────
    ("antwerp",             "Antwerp",           "Belgium"),
    ("brussels",            "Brussels",          "Belgium"),
    ("bruselas",            "Brussels",          "Belgium"),

    # ── Spain ────────────────────────────────────────────────────────────
    ("madrid",              "Madrid",            "Spain"),
    ("barcelona",           "Barcelona",         "Spain"),

    # ── Italy ────────────────────────────────────────────────────────────
    ("milan",               "Milan",             "Italy"),
    ("rome",                "Rome",              "Italy"),
    ("turin",               "Turin",             "Italy"),

    # ── Poland ───────────────────────────────────────────────────────────
    ("warsaw",              "Warsaw",            "Poland"),
    ("krakow",              "Krakow",            "Poland"),
    ("poznan",              "Poznań",            "Poland"),
    ("wroclaw",             "Wrocław",           "Poland"),

    # ── Hungary ──────────────────────────────────────────────────────────
    ("budapest",            "Budapest",          "Hungary"),
    ("szeged",              "Szeged",            "Hungary"),

    # ── Czech Republic ───────────────────────────────────────────────────
    ("prague",              "Prague",            "Czech Republic"),

    # ── Romania ──────────────────────────────────────────────────────────
    ("bucharest",           "Bucharest",         "Romania"),

    # ── Greece ───────────────────────────────────────────────────────────
    ("athens",              "Athens",            "Greece"),

    # ── Switzerland ──────────────────────────────────────────────────────
    ("zurich",              "Zurich",            "Switzerland"),
    ("geneva",              "Geneva",            "Switzerland"),
    ("basel",               "Basel",             "Switzerland"),

    # ── Luxembourg ───────────────────────────────────────────────────────
    ("luxembourg",          "Luxembourg City",   "Luxembourg"),

    # ── Austria ──────────────────────────────────────────────────────────
    ("vienna",              "Vienna",            "Austria"),
    ("salzburg",            "Salzburg",          "Austria"),

    # ── Portugal ─────────────────────────────────────────────────────────
    ("lisbon",              "Lisbon",            "Portugal"),
    ("porto",               "Porto",             "Portugal"),

    # ── Nordics ──────────────────────────────────────────────────────────
    ("stockholm",           "Stockholm",         "Sweden"),
    ("gothenburg",          "Gothenburg",        "Sweden"),
    ("oslo",                "Oslo",              "Norway"),
    ("bergen",              "Bergen",            "Norway"),
    ("copenhagen",          "Copenhagen",        "Denmark"),
    ("aarhus",              "Aarhus",            "Denmark"),
    ("koege",               "Køge",              "Denmark"),
    ("helsinki",            "Helsinki",          "Finland"),

    # ── Baltics ──────────────────────────────────────────────────────────
    ("tallinn",             "Tallinn",           "Estonia"),
    ("riga",                "Riga",              "Latvia"),
    ("vilnius",             "Vilnius",           "Lithuania"),

    # ── Middle East ──────────────────────────────────────────────────────
    ("dubai",               "Dubai",             "UAE"),
    ("abu dhabi",           "Abu Dhabi",         "UAE"),
    ("doha",                "Doha",              "Qatar"),
    ("riyadh",              "Riyadh",            "Saudi Arabia"),
    ("jeddah",              "Jeddah",            "Saudi Arabia"),
    ("manama",              "Manama",            "Bahrain"),
    ("kuwait city",         "Kuwait City",       "Kuwait"),

    # ── Israel ───────────────────────────────────────────────────────────
    ("tel aviv",            "Tel Aviv",          "Israel"),
    ("jerusalem",           "Jerusalem",         "Israel"),
    ("haifa",               "Haifa",             "Israel"),

    # ── Africa ───────────────────────────────────────────────────────────
    ("johannesburg",        "Johannesburg",      "South Africa"),
    ("cape town",           "Cape Town",         "South Africa"),
    ("sandton",             "Sandton",           "South Africa"),
    ("cairo",               "Cairo",             "Egypt"),
    ("nairobi",             "Nairobi",           "Kenya"),
    ("lagos",               "Lagos",             "Nigeria"),
    ("casablanca",          "Casablanca",        "Morocco"),

    # ── Asia ─────────────────────────────────────────────────────────────
    ("singapore",           "Singapore",         "Singapore"),
    ("hong kong",           "Hong Kong",         "Hong Kong"),
    ("hk-",                 "Hong Kong",         "Hong Kong"),
    ("gurugram",            "Gurugram",          "India"),
    ("gurgaon",             "Gurugram",          "India"),
    ("noida",               "Noida",             "India"),
    ("mumbai",              "Mumbai",            "India"),
    ("new delhi",           "New Delhi",         "India"),
    ("delhi",               "New Delhi",         "India"),
    ("bangalore",           "Bangalore",         "India"),
    ("bengaluru",           "Bangalore",         "India"),
    ("hyderabad",           "Hyderabad",         "India"),
    ("pune",                "Pune",              "India"),
    ("chennai",             "Chennai",           "India"),
    ("kolkata",             "Kolkata",           "India"),
    ("shanghai",            "Shanghai",          "China"),
    ("beijing",             "Beijing",           "China"),
    ("shenzhen",            "Shenzhen",          "China"),
    ("tokyo",               "Tokyo",             "Japan"),
    ("osaka",               "Osaka",             "Japan"),
    ("seoul",               "Seoul",             "South Korea"),
    ("kuala lumpur",        "Kuala Lumpur",       "Malaysia"),
    ("jakarta",             "Jakarta",           "Indonesia"),
    ("bangkok",             "Bangkok",           "Thailand"),
    ("taipei",              "Taipei",            "Taiwan"),
    ("manila",              "Manila",            "Philippines"),
    ("ho chi minh",         "Ho Chi Minh City",  "Vietnam"),

    # ── Oceania ──────────────────────────────────────────────────────────
    ("sydney",              "Sydney",            "Australia"),
    ("melbourne",           "Melbourne",         "Australia"),
    ("brisbane",            "Brisbane",          "Australia"),
    ("perth",               "Perth",             "Australia"),

    # ── South America ────────────────────────────────────────────────────
    ("sao paulo",           "São Paulo",         "Brazil"),
    ("rio de janeiro",      "Rio de Janeiro",    "Brazil"),
    ("bogota",              "Bogotá",            "Colombia"),
    ("santiago",            "Santiago",          "Chile"),
    ("buenos aires",        "Buenos Aires",      "Argentina"),
    ("mexico city",         "Mexico City",       "Mexico"),
    ("ciudad de mexico",    "Mexico City",       "Mexico"),

    # ── Bermuda ──────────────────────────────────────────────────────────
    ("bermuda",             "Hamilton",          "Bermuda"),
]

# Use YAML if available, otherwise hardcoded defaults
if _yaml_result is not None:
    _RULES, _COUNTRY_ONLY_FROM_YAML, _ALLOWED_FROM_YAML = _yaml_result
else:
    _RULES = _DEFAULT_RULES
    _COUNTRY_ONLY_FROM_YAML = None
    _ALLOWED_FROM_YAML = None

_RULES_LOWER = [(kw.lower(), city, country) for kw, city, country in _RULES]

# ══════════════════════════════════════════════════════════════════════════
# COUNTRY-CONSTRAINED CITY LOOKUP (built from _RULES)
# ══════════════════════════════════════════════════════════════════════════
# Maps country → [(keyword, canonical_city), ...] for scanning a city part
# within a known country context.

_CITIES_FOR_COUNTRY: dict[str, list[tuple[str, str]]] = defaultdict(list)
for _kw, _city, _ctry in _RULES_LOWER:
    _CITIES_FOR_COUNTRY[_ctry].append((_kw, _city))
_CITIES_FOR_COUNTRY = dict(_CITIES_FOR_COUNTRY)  # freeze

# Cities that exist in multiple countries — these should NOT match in the
# unconstrained rules pass. They require country context to resolve.
_AMBIGUOUS_KEYWORDS: set[str] = set()
_kw_countries: dict[str, set[str]] = defaultdict(set)
for _kw, _city, _ctry in _RULES_LOWER:
    _kw_countries[_kw].add(_ctry)
for _kw, _countries in _kw_countries.items():
    if len(_countries) > 1:
        _AMBIGUOUS_KEYWORDS.add(_kw)
del _kw_countries  # cleanup

# ── Country-only fallback (no city info, just country name in string) ────
# 2026-04-22: extended with native-language aliases ("Deutschland",
# "Italia") after the audit of 159K enriched rows surfaced these
# falling through to country=None. Also added several out-of-region
# country names we see in aggregator feeds so they resolve to the
# right country and get geo_filtered cleanly rather than sitting in
# the DB as "enriched with no location".
_DEFAULT_COUNTRY_ONLY: dict[str, str] = {
    # Allowed markets
    "united kingdom": "UK", "england": "UK", "scotland": "UK", "wales": "UK",
    "northern ireland": "UK", "britain": "UK", "great britain": "UK",
    "united states": "USA", "america": "USA", "united states of america": "USA",
    "canada": "Canada",
    "france": "France",
    "germany": "Germany", "deutschland": "Germany",
    "switzerland": "Switzerland", "schweiz": "Switzerland", "suisse": "Switzerland",
    "netherlands": "Netherlands", "nederland": "Netherlands", "holland": "Netherlands",
    "luxembourg": "Luxembourg",
    "uae": "UAE", "united arab emirates": "UAE",
    "saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia",
    "singapore": "Singapore",
    "hong kong": "Hong Kong", "hong kong sar": "Hong Kong",
    # Adjacent Europe (not allowed but shouldn't silently fall through)
    "ireland": "Ireland",
    "italy": "Italy", "italia": "Italy",
    "spain": "Spain", "españa": "Spain", "espana": "Spain",
    "poland": "Poland", "polska": "Poland",
    "belgium": "Belgium", "belgië": "Belgium", "belgique": "Belgium",
    "austria": "Austria", "österreich": "Austria",
    "portugal": "Portugal",
    "hungary": "Hungary",
    "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "romania": "Romania",
    "greece": "Greece",
    "finland": "Finland",
    "sweden": "Sweden", "sverige": "Sweden",
    "norway": "Norway", "norge": "Norway",
    "denmark": "Denmark", "danmark": "Denmark",
    "croatia": "Croatia", "bulgaria": "Bulgaria", "serbia": "Serbia",
    "latvia": "Latvia", "lithuania": "Lithuania", "estonia": "Estonia",
    "iceland": "Iceland", "cyprus": "Cyprus", "malta": "Malta",
    "russia": "Russia", "ukraine": "Ukraine",
    # Asia-Pac
    "australia": "Australia", "new zealand": "New Zealand",
    "india": "India", "china": "China", "japan": "Japan",
    "south korea": "South Korea", "korea": "South Korea",
    "philippines": "Philippines", "indonesia": "Indonesia",
    "vietnam": "Vietnam", "malaysia": "Malaysia", "thailand": "Thailand",
    "pakistan": "Pakistan", "bangladesh": "Bangladesh",
    "sri lanka": "Sri Lanka",
    # Middle East / Africa
    "israel": "Israel", "qatar": "Qatar", "bahrain": "Bahrain",
    "kuwait": "Kuwait", "oman": "Oman", "turkey": "Turkey",
    "lebanon": "Lebanon", "jordan": "Jordan", "egypt": "Egypt",
    "south africa": "South Africa", "nigeria": "Nigeria",
    "kenya": "Kenya", "ghana": "Ghana", "morocco": "Morocco",
    # Latin America
    "brazil": "Brazil", "brasil": "Brazil",
    "mexico": "Mexico", "méxico": "Mexico",
    "argentina": "Argentina", "colombia": "Colombia",
    "chile": "Chile", "peru": "Peru", "perú": "Peru",
    "venezuela": "Venezuela", "dominican republic": "Dominican Republic",
}
# Merge strategy: start with Python defaults (the broadest set including
# native-language aliases like "deutschland"/"polska"/"italia"), then
# overlay any entries from YAML. This lets the YAML config customise
# keys without having to re-declare the whole native-language / multi-
# lingual alias set every time the Python defaults grow.
_COUNTRY_ONLY = dict(_DEFAULT_COUNTRY_ONLY)
if _COUNTRY_ONLY_FROM_YAML:
    _COUNTRY_ONLY.update(_COUNTRY_ONLY_FROM_YAML)

# ── Remote / hybrid detection ────────────────────────────────────────────
_REMOTE_RE = re.compile(
    r"\b(remote|hybrid|work from home|home[\s\-]based|home[\s\-]workers?"
    r"|home worker|virtual|anywhere)\b"
    r"|home\s*[-–]\s*uk",
    re.IGNORECASE,
)

# ── US state codes & names for fallback ──────────────────────────────────
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "MA",
    "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE",
    "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
}

_US_STATE_NAMES: dict[str, str] = {
    "alabama": "USA", "alaska": "USA", "arizona": "USA", "arkansas": "USA",
    "california": "USA", "colorado": "USA", "connecticut": "USA", "delaware": "USA",
    "florida": "USA", "georgia": "USA", "hawaii": "USA", "idaho": "USA",
    "illinois": "USA", "indiana": "USA", "iowa": "USA", "kansas": "USA",
    "kentucky": "USA", "louisiana": "USA", "maine": "USA", "maryland": "USA",
    "massachusetts": "USA", "michigan": "USA", "minnesota": "USA", "mississippi": "USA",
    "missouri": "USA", "montana": "USA", "nebraska": "USA", "nevada": "USA",
    "new hampshire": "USA", "new jersey": "USA", "new mexico": "USA", "new york": "USA",
    "north carolina": "USA", "north dakota": "USA", "ohio": "USA", "oklahoma": "USA",
    "oregon": "USA", "pennsylvania": "USA", "rhode island": "USA", "south carolina": "USA",
    "south dakota": "USA", "tennessee": "USA", "texas": "USA", "utah": "USA",
    "vermont": "USA", "virginia": "USA", "washington": "USA", "west virginia": "USA",
    "wisconsin": "USA", "wyoming": "USA", "district of columbia": "USA",
}

_CANADIAN_PROVINCES: dict[str, str] = {
    "ontario": "Canada", "quebec": "Canada", "british columbia": "Canada",
    "alberta": "Canada", "manitoba": "Canada", "saskatchewan": "Canada",
    "nova scotia": "Canada", "new brunswick": "Canada",
}

# ── 2-letter country codes for structured parse ─────────────────────────
# Enables the SmartRecruiters-style "City, COUNTRY, iso-2" format
# (e.g. "Pasay City, PHILIPPINES, ph") to resolve via the existing
# step-2 structured parse — the trailing ISO-2 is tried first as the
# authoritative country marker. Adding the broader set (2026-04-22)
# after an audit showed ~50+ tri-part rows silently failing because
# their ISO codes weren't mapped. Out-of-region countries (Philippines,
# Indonesia, etc.) still get geo_filtered correctly by the enrichment
# pipeline — but at least the country is now resolved, not left as
# None with "raw location present but enrichment failed" in the DB.
_COUNTRY_CODES: dict[str, str] = {
    # Allowed markets
    "us": "USA", "uk": "UK", "gb": "UK", "ca": "Canada",
    "sg": "Singapore", "hk": "Hong Kong",
    "de": "Germany", "fr": "France", "nl": "Netherlands",
    "ch": "Switzerland", "lu": "Luxembourg",
    "ae": "UAE", "sa": "Saudi Arabia",
    # Adjacent Europe
    "ie": "Ireland", "it": "Italy", "es": "Spain",
    "at": "Austria", "be": "Belgium", "pt": "Portugal",
    "pl": "Poland", "se": "Sweden", "no": "Norway", "dk": "Denmark",
    "fi": "Finland", "cz": "Czech Republic",
    "hu": "Hungary", "ro": "Romania", "gr": "Greece",
    # Gulf / Middle East
    "il": "Israel", "qa": "Qatar", "bh": "Bahrain", "kw": "Kuwait",
    "om": "Oman", "tr": "Turkey", "lb": "Lebanon", "jo": "Jordan",
    "eg": "Egypt",
    # Asia-Pac (tri-part audit targets)
    "in": "India", "cn": "China", "jp": "Japan", "kr": "South Korea",
    "au": "Australia", "nz": "New Zealand",
    "ph": "Philippines", "id": "Indonesia", "vn": "Vietnam",
    "my": "Malaysia", "th": "Thailand", "pk": "Pakistan",
    "lk": "Sri Lanka", "bd": "Bangladesh",
    # Africa (SmartRecruiters tri-part targets)
    "za": "South Africa", "ng": "Nigeria", "ke": "Kenya",
    "ci": "Côte d'Ivoire", "sn": "Senegal", "cm": "Cameroon",
    "gh": "Ghana", "tz": "Tanzania", "ma": "Morocco", "dz": "Algeria",
    # Latin America
    "br": "Brazil", "mx": "Mexico", "ar": "Argentina",
    "co": "Colombia", "cl": "Chile", "pe": "Peru",
    "ve": "Venezuela", "do": "Dominican Republic",
    "uy": "Uruguay", "py": "Paraguay", "ec": "Ecuador",
    "gt": "Guatemala", "pa": "Panama", "cr": "Costa Rica",
    # Rest of Europe we occasionally see
    "ru": "Russia", "ua": "Ukraine",
}

# ── UK counties/regions (for "Town, County" → UK detection) ─────────────
_UK_COUNTIES: set[str] = {
    "berkshire", "buckinghamshire", "cambridgeshire", "cheshire", "cornwall", "cumbria",
    "derbyshire", "devon", "dorset", "durham", "east sussex", "essex", "fife",
    "gloucestershire", "hampshire", "herefordshire", "hertfordshire", "kent",
    "lancashire", "leicestershire", "lincolnshire", "merseyside", "midlothian",
    "norfolk", "north lanarkshire", "north somerset", "north yorkshire",
    "northamptonshire", "northumberland", "nottinghamshire", "oxfordshire",
    "renfrewshire", "rutland", "shropshire", "somerset", "south ayrshire",
    "south lanarkshire", "south yorkshire", "staffordshire", "suffolk", "surrey",
    "tyne & wear", "tyne and wear", "warwickshire", "west berkshire", "west midlands",
    "west sussex", "west yorkshire", "wiltshire", "worcestershire",
    "scottish borders", "argyll & bute", "argyll and bute", "highland",
    "moray", "perth & kinross", "perth and kinross", "angus", "aberdeenshire",
    "east lothian", "west lothian", "falkirk", "stirling", "clackmannanshire",
    "dumfries & galloway", "dumfries and galloway", "inverclyde",
    "rhondda cynon taff", "flintshire", "torfaen", "caerphilly", "carmarthenshire",
    "pembrokeshire", "ceredigion", "powys", "gwynedd", "conwy", "denbighshire",
    "wrexham", "vale of glamorgan", "bridgend", "neath port talbot", "swansea",
    "ards", "antrim", "armagh", "down", "fermanagh", "londonderry", "tyrone",
    "east riding of yorkshire",
}

# ── Canadian region names (for "City, Region" → Canada detection) ───────
_CANADIAN_REGION_NAMES: set[str] = {
    "peel region", "halton", "waterloo region", "simcoe region", "york region",
    "durham region", "niagara", "algoma", "bruce region",
    "wellington region", "dufferin", "brant region",
    "cochrane", "kenora", "nipissing", "parry sound", "sudbury", "thunder bay",
    "timiskaming", "montréal", "laval", "laurentides", "lanaudière",
    "mauricie", "francheville", "deux-montagnes", "champlain",
    "fraser valley", "central okanagan", "north okanagan", "columbia-shuswap",
    "squamish-lillooet", "comox-strathcona", "alberni-clayoquot", "mount waddington",
    "bulkley-nechako", "kitimat-stikine", "peace river region", "powell river region",
    "sunshine coast", "cowichan valley", "capital", "nanaimo region",
    "fraser-fort george", "cariboo", "thompson-nicola", "east kootenay",
    "central kootenay", "kootenay boundary", "okanagan-similkameen",
    "regina region", "saskatoon", "prince albert", "moose jaw",
    "westmorland", "saint john region", "saint george", "conception bay",
    "yellowhead", "big lakes", "lennox and addington",
}

# ── UK known towns (for Reed-style bare-name fallback) ─────────────────
# Reed, Adzuna and some direct-ATS adapters routinely give a bare UK
# town/borough name with no country marker ("Heywood", "Egham",
# "Lisvane"). Without this lookup those strings fall through to
# country=None even though they're unambiguously UK. Curated from the
# 1,500+ bare-name failures surfaced by the 2026-04-22 audit — biased
# toward London boroughs, Home Counties commuter towns, and regional
# UK cities (the things we actually see in practice). Case-insensitive
# match against `.lower()`.
#
# Deliberately NOT a full UK gazetteer — only entries that (a) have
# appeared as a raw_location in our DB and (b) aren't ambiguous with
# non-UK places of the same name. The list is additive over time as
# new failures surface in audit.
_UK_KNOWN_TOWNS: set[str] = {
    # London boroughs / inner & outer areas
    "hampstead", "stepney", "hillingdon", "merton", "kensington", "chelsea",
    "westminster", "camden", "islington", "hackney", "tower hamlets",
    "newham", "greenwich", "lewisham", "southwark", "lambeth", "wandsworth",
    "ealing", "brent", "barnet", "haringey", "enfield", "waltham forest",
    "redbridge", "havering", "bexley", "bromley", "croydon", "sutton",
    "hammersmith", "fulham", "paddington", "marylebone", "mayfair", "soho",
    "shoreditch", "whitechapel", "canary wharf", "docklands", "stratford",
    "wimbledon", "clapham", "brixton", "peckham", "dulwich",
    "twickenham", "wembley", "harrow", "ruislip", "uxbridge",
    "walthamstow", "leyton", "ilford", "romford", "dagenham", "barking",
    # London commuter belt / Home Counties
    "egham", "staines", "weybridge", "woking", "guildford", "epsom",
    "reigate", "redhill", "dorking", "leatherhead", "cobham",
    "kingston", "richmond", "surbiton", "tolworth",
    "watford", "rickmansworth", "chorleywood", "amersham", "chesham",
    "high wycombe", "maidenhead", "windsor", "slough", "ascot",
    "ockendon", "grays", "basildon", "chelmsford",
    "rainham", "gillingham", "chatham", "rochester", "gravesend",
    "dartford", "orpington", "sevenoaks", "tunbridge wells", "tonbridge",
    "waltham cross", "hoddesdon", "cheshunt", "hertford", "stevenage",
    "welwyn garden city", "hatfield", "st albans", "hemel hempstead",
    # Greater Manchester / North West
    "heywood", "rochdale", "oldham", "bury", "bolton", "wigan", "salford",
    "stockport", "trafford", "tameside", "warrington", "burnley",
    "blackburn", "preston", "blackpool", "lytham", "st helens", "widnes",
    # Yorkshire / North East
    "wakefield", "doncaster", "barnsley", "rotherham", "huddersfield",
    "halifax", "bradford", "dewsbury", "keighley", "pontefract",
    "middlesbrough", "stockton-on-tees", "hartlepool", "darlington",
    "sunderland", "gateshead", "washington", "durham",
    # West Midlands / South West
    "wolverhampton", "dudley", "walsall", "west bromwich", "smethwick",
    "solihull", "redditch", "bromsgrove", "kidderminster",
    "stoke-on-trent", "stafford", "telford", "shrewsbury", "hereford",
    "cheltenham", "gloucester", "swindon", "bath", "bristol",
    "weston-super-mare", "taunton", "yeovil", "exeter", "plymouth",
    "torquay", "bournemouth", "poole", "southampton", "portsmouth",
    "fareham", "gosport", "chichester", "worthing", "brighton", "hove",
    "eastbourne", "hastings", "crawley", "horsham", "haywards heath",
    # East of England
    "norwich", "great yarmouth", "ipswich", "colchester", "bury st edmunds",
    "cambridge", "peterborough", "huntingdon", "ely", "wisbech",
    # East Midlands
    "nottingham", "derby", "leicester", "lincoln", "boston", "skegness",
    "mansfield", "chesterfield", "worksop",
    # Wales
    "cardiff", "swansea", "newport", "wrexham", "lisvane", "caerphilly",
    "pontypridd", "merthyr tydfil", "bridgend", "port talbot",
    # Scotland
    "dundee", "aberdeen", "inverness", "stirling", "perth", "paisley",
    "motherwell", "hamilton", "kilmarnock", "ayr", "dumfries",
    "kirkcaldy", "dunfermline", "livingston", "east kilbride",
    # Northern Ireland
    "lisburn", "newtownabbey", "bangor", "craigavon", "ballymena",
    "newry", "antrim", "coleraine", "omagh",
}


# ── Multi-site sentinel values ────────────────────────────────────────
# Strings that mean "this posting can be any of several locations"
# rather than a specific place. Surfaced 2026-04-22 as a common
# cause of silent country=None rows for generic_site / Workday leads
# ("Multiple", "All Locations", "+9More Locations", "Flexible",
# "@one Sites" etc.). Returns city="Multiple" + country=None so:
#   1. The string is recognisably multi-site in DB / export rather
#      than indistinguishable from an unparseable failure.
#   2. is_allowed_country(None) still returns True, so the row isn't
#      geo_filtered — these postings are real leads, just
#      location-ambiguous.
_MULTI_SITE_SENTINELS: set[str] = {
    "multiple", "multiple locations", "multiple location", "mulitple locations",
    "all locations", "all location", "all sites",
    "various", "various locations", "various sites",
    "flexible", "flexible location", "flexible locations",
    "cross site", "cross-site", "multi-site", "multi site",
    "nationwide", "uk wide", "uk-wide", "nationally",
    "@one sites", "various uk", "multiple uk", "multiple us",
}
# Matches "+9More Locations", "and 2 more", "+1 more", etc.
_MULTI_SITE_REGEX = re.compile(r'^(?:and\s+|\+)?\d+\s*more\s*(?:locations?|sites?)?$', re.I)


# ── German (Kreis) / county pattern ──────────────────────────────────
# Adzuna produces this format prolifically for German listings —
# "Walsrode, Soltau-Fallingbostel (Kreis)" or "Heidenheim (Kreis),
# Baden-Württemberg". Germany is an allowed country; these rows
# were falling through to country=None because (Kreis) isn't a
# country token any rule recognised. A single regex catches all of
# them and returns the first comma-part as the city, stripped of
# "(Kreis)" if present. Recovers ~3,500 rows (audit 2026-04-22).
_GERMAN_KREIS_RE = re.compile(r'\(\s*kreis\s*\)', re.I)


# ── Target regions ───────────────────────────────────────────────────────
_DEFAULT_ALLOWED_COUNTRIES: set[str] = {
    "USA", "Canada",
    "UK",
    "France", "Germany", "Switzerland", "Netherlands", "Luxembourg",
    "UAE", "Saudi Arabia",
    "Singapore", "Hong Kong",
}
ALLOWED_COUNTRIES = _ALLOWED_FROM_YAML if _ALLOWED_FROM_YAML else _DEFAULT_ALLOWED_COUNTRIES

# ── Scrub helper ─────────────────────────────────────────────────────────
_STRIP_PHRASES = re.compile(
    r"\b(thrive apprenticeship|contributors to your success|quantitative skills"
    r"|employs over|global reach|head of product"
    r"|gb\b|ie\b|us\b(?!\s*[a-z])|gbr\b|aus\b)\b",
    re.IGNORECASE,
)


def _scrub(raw: str) -> str:
    """Normalise whitespace, strip artefacts and non-geographic noise."""
    s = re.sub(r'\s+', ' ', raw).strip()
    s = re.sub(r'^[a-z]\s+', '', s, flags=re.IGNORECASE)
    s = _STRIP_PHRASES.sub(' ', s)
    s = re.sub(r'\s+', ' ', s).strip().strip(',').strip()
    return s


# ══════════════════════════════════════════════════════════════════════════
# STRUCTURED PARSE: resolve country from trailing token BEFORE city lookup
# ══════════════════════════════════════════════════════════════════════════

_CANADIAN_PROVINCE_CODES: dict[str, str] = {
    "on": "Canada", "qc": "Canada", "bc": "Canada", "ab": "Canada",
    "mb": "Canada", "sk": "Canada", "ns": "Canada", "nb": "Canada",
    "pe": "Canada", "nl": "Canada", "nt": "Canada", "nu": "Canada", "yt": "Canada",
}

# Country codes that conflict with US state codes — these should be
# resolved as state codes when they appear after a comma (e.g. "Chicago, IL"
# means Illinois, not Israel).
_STATE_COUNTRY_CONFLICTS = _US_STATE_CODES & {c.upper() for c in _COUNTRY_CODES}
# Also conflicts with Canadian province codes
_STATE_PROVINCE_CONFLICTS = {c.upper() for c in _CANADIAN_PROVINCE_CODES} & {c.upper() for c in _COUNTRY_CODES}


def _resolve_country_token(token: str) -> str | None:
    """Resolve a single token (the last part after splitting on comma) to a country.

    Checks in order: US state codes (→ USA), Canadian province codes (→ Canada),
    2-letter country codes, US state names, Canadian province names, country names.

    US state codes and Canadian province codes are checked BEFORE country codes
    because in "City, XX" format, XX is overwhelmingly a state/province, not a country.
    """
    stripped = token.strip()
    upper = stripped.upper()
    lower = stripped.lower()

    # Check if this is both a US state code AND a country code (e.g. "IN" = Indiana/India, "DE" = Delaware/Germany)
    is_state = upper in _US_STATE_CODES
    is_country = lower in _COUNTRY_CODES
    is_province = lower in _CANADIAN_PROVINCE_CODES

    if is_state and not is_country:
        # Unambiguous US state code (TX, CA, NY, IL, AL, etc.)
        return "USA"

    if is_province and not is_country:
        # Unambiguous Canadian province code (ON, QC, BC, etc.)
        return "Canada"

    if is_country and not is_state and not is_province:
        # Unambiguous country code (UK, US, FR, etc.)
        return _COUNTRY_CODES[lower]

    if is_country and (is_state or is_province):
        # Ambiguous: could be state/province or country.
        # Default preference: country wins — "DE" = Germany, "IN" = India,
        # "CA" = Canada — because in 2-part recruiter feeds these are
        # overwhelmingly country codes, not state codes.
        # Exceptions where state/province wins:
        #   IL — Illinois >> Israel
        #   MA — Massachusetts >> Morocco
        #   ID — Idaho >> Indonesia (in 2-part; tri-part hits parts[-2] first)
        #   CO — Colorado >> Colombia
        #   AR — Arkansas >> Argentina
        #   PA — Pennsylvania >> Panama
        #   PE — Prince Edward Island >> Peru (province conflict)
        # Tri-part "City, Country, iso-2" strings sidestep this because
        # normalise_location() checks parts[-2] first for those.
        _STATE_OR_PROVINCE_WINS = {"IL", "MA", "ID", "CO", "AR", "PA", "PE"}
        if upper in _STATE_OR_PROVINCE_WINS:
            return "Canada" if is_province else "USA"
        return _COUNTRY_CODES[lower]

    # US state name: "Alabama", "California", etc.
    if lower in _US_STATE_NAMES:
        return "USA"

    # Canadian province name: "Ontario", "Quebec", etc.
    if lower in _CANADIAN_PROVINCES:
        return "Canada"

    # Full country name: "United States", "Germany", etc.
    if lower in _COUNTRY_ONLY:
        return _COUNTRY_ONLY[lower]

    return None


def _find_city_in_country(text: str, country: str) -> str | None:
    """Find a city keyword in text constrained to a specific country."""
    lower = text.lower()
    candidates = _CITIES_FOR_COUNTRY.get(country, [])
    for keyword, canonical_city in candidates:
        if keyword in lower:
            return canonical_city
    return None


def normalise_location(location_raw: str | None) -> dict:
    """
    Resolve a raw location string to (city, country).

    Strategy:
    1. Remote/hybrid detection with country context
    2. Structured parse — split on commas, resolve country from trailing
       parts, then find city constrained by that country
    3. Rules-based substring matching (fallback for unstructured strings,
       skips ambiguous city keywords)
    4. Country-only fallback
    5. UK postcode detection
    6. US state name as bare string
    7. Unresolved
    """
    if not location_raw or location_raw.strip() in ('', 'N/A'):
        return {"raw": location_raw, "city": None, "region": None, "country": None, "confidence": 0.0}

    # ── 0. Bare country name check BEFORE scrubbing (scrub strips "US") ──
    raw_lower = location_raw.strip().lower()
    if raw_lower in ("uk", "united kingdom", "england"):
        return {"raw": location_raw, "city": "UK", "region": None, "country": "UK", "confidence": 0.7}
    if raw_lower in ("usa", "us", "u.s.", "u.s.a.", "united states", "united states of america"):
        return {"raw": location_raw, "city": "USA", "region": None, "country": "USA", "confidence": 0.7}
    if raw_lower in ("canada",):
        return {"raw": location_raw, "city": "Canada", "region": None, "country": "Canada", "confidence": 0.7}
    if raw_lower in _COUNTRY_ONLY:
        c = _COUNTRY_ONLY[raw_lower]
        return {"raw": location_raw, "city": c, "region": None, "country": c, "confidence": 0.7}

    cleaned = _scrub(location_raw)
    lower = cleaned.lower()

    # ── 0a. Multi-site sentinel values ────────────────────────────────
    # "Multiple", "All Locations", "+9More Locations" — postings that
    # span multiple sites. Return city="Multiple", country=None so:
    #   (a) the row is visibly multi-site in DB / exports rather than
    #       indistinguishable from a parse failure, and
    #   (b) is_allowed_country(None) → True, so the lead isn't
    #       geo_filtered. These are real leads in an allowed market,
    #       just location-ambiguous.
    if lower in _MULTI_SITE_SENTINELS or _MULTI_SITE_REGEX.match(lower):
        return {"raw": location_raw, "city": "Multiple", "region": None, "country": None, "confidence": 0.2}

    # ── 1. Remote / hybrid ───────────────────────────────────────────────
    if _REMOTE_RE.search(lower):
        if any(t in lower for t in ('uk', 'england', 'britain', 'london', 'scotland')):
            return {"raw": location_raw, "city": "Remote", "region": None, "country": "UK", "confidence": 0.7}
        if any(t in lower for t in ('united states', 'america', ' us ', 'usa')):
            return {"raw": location_raw, "city": "Remote", "region": None, "country": "USA", "confidence": 0.7}
        if 'canada' in lower:
            return {"raw": location_raw, "city": "Remote", "region": None, "country": "Canada", "confidence": 0.7}
        # Remote with no country context — default to UK
        return {"raw": location_raw, "city": "Remote", "region": None, "country": "UK", "confidence": 0.4}

    # ── 1b. US county pattern: "City, XX County" → USA ───────────────
    _county_match = re.match(r'^(.+?),\s+(.+?\s+county)$', cleaned, re.I)
    if _county_match:
        city_part = _county_match.group(1).strip()
        return {"raw": location_raw, "city": city_part, "region": None, "country": "USA", "confidence": 0.85}

    # ── 1c. German Adzuna "(Kreis)" pattern → Germany ──────────────
    # Catches "Heidenheim (Kreis), Baden-Württemberg" and
    # "Walsrode, Soltau-Fallingbostel (Kreis)" style strings.
    # Takes the first comma-part as the city; strips the "(Kreis)"
    # suffix so the city displays cleanly. Recovers ~3.5K rows
    # (2026-04-22 audit).
    if _GERMAN_KREIS_RE.search(cleaned):
        kreis_city = _GERMAN_KREIS_RE.sub('', cleaned.split(',')[0]).strip()
        if kreis_city:
            return {"raw": location_raw, "city": kreis_city, "region": None, "country": "Germany", "confidence": 0.85}

    # ── 1d. UK county pattern: "City, County XX" (e.g. "Dungannon, County Tyrone") ──
    _uk_county_match = re.match(r'^(.+?),\s+county\s+', cleaned, re.I)
    if _uk_county_match:
        city_part = _uk_county_match.group(1).strip()
        return {"raw": location_raw, "city": city_part, "region": None, "country": "UK", "confidence": 0.85}

    # ── 1e. UK county/region in trailing part: "Taunton, Somerset" → UK ──
    _comma_parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if len(_comma_parts) >= 2:
        trailing = _comma_parts[-1].strip().lower()
        if trailing in _UK_COUNTIES:
            return {"raw": location_raw, "city": _comma_parts[0], "region": None, "country": "UK", "confidence": 0.85}
        if trailing in _CANADIAN_REGION_NAMES:
            return {"raw": location_raw, "city": _comma_parts[0], "region": None, "country": "Canada", "confidence": 0.85}

    # ── 2. Structured parse: split and resolve country FIRST ─────────────
    # For tri-part "City, Country-name, iso-2" strings (SmartRecruiters
    # / Workday format — "Pasay City, PHILIPPINES, ph"), the trailing
    # 2-letter code can collide with a US state or Canadian province
    # ("id" = Indonesia but "ID" = Idaho; "ca" = Canada but "CA" =
    # California). When that happens and parts[-2] holds an unambiguous
    # country NAME, prefer it — the full name is always more
    # authoritative than the 2-letter code. For 2-part strings
    # ("Cambridge, MA") parts[-1] is the only signal, and
    # `_resolve_country_token`'s state-wins list handles the tiebreak.
    parts = [p.strip() for p in re.split(r'[,|/]', cleaned) if p.strip()]
    if len(parts) >= 2:
        last_token = parts[-1].strip()
        last_lower = last_token.lower()
        last_upper = last_token.upper()
        last_is_ambiguous_2letter = (
            len(last_upper) == 2
            and last_lower in _COUNTRY_CODES
            and (last_upper in _US_STATE_CODES or last_lower in _CANADIAN_PROVINCE_CODES)
        )
        if len(parts) >= 3 and last_is_ambiguous_2letter:
            resolved_country = (
                _resolve_country_token(parts[-2])
                or _resolve_country_token(parts[-1])
            )
        else:
            resolved_country = _resolve_country_token(parts[-1])
            if not resolved_country and len(parts) >= 3:
                resolved_country = _resolve_country_token(parts[-2])

        if resolved_country:
            # Try to find city constrained to this country
            city_text = parts[0]
            constrained_city = _find_city_in_country(city_text, resolved_country)
            if constrained_city:
                return {"raw": location_raw, "city": constrained_city, "region": None, "country": resolved_country, "confidence": 0.95}
            # Country known, use first part as-is for city
            return {"raw": location_raw, "city": city_text, "region": None, "country": resolved_country, "confidence": 0.85}

    # ── 3. Rules-based city matching (skip ambiguous keywords) ───────────
    for keyword, city, country in _RULES_LOWER:
        if keyword in _AMBIGUOUS_KEYWORDS:
            continue  # These need country context — handled in step 2 above
        if keyword in lower:
            return {"raw": location_raw, "city": city, "region": None, "country": country, "confidence": 0.9}

    # ── 4. Country-only fallback ─────────────────────────────────────────
    for phrase, country in _COUNTRY_ONLY.items():
        if phrase in lower:
            return {"raw": location_raw, "city": country, "region": None, "country": country, "confidence": 0.7}

    # ── 5. UK postcode detection (e.g. EC2M7EA, LS1 4PR, SW1A 1AA) ──────
    if re.match(r'^[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}$', cleaned, re.I):
        return {"raw": location_raw, "city": cleaned, "region": None, "country": "UK", "confidence": 0.75}

    # ── 6. US state name as bare string: "Massachusetts" ───────────────
    if lower in _US_STATE_NAMES:
        return {"raw": location_raw, "city": cleaned, "region": None, "country": "USA", "confidence": 0.75}

    # ── 7. Single-part ambiguous city with no country context ────────────
    # If the string is just "Birmingham" with no comma/context, use the
    # first matching rule (which preserves the original ordering — UK first
    # for most ambiguous cities, since that's the primary market).
    for keyword, city, country in _RULES_LOWER:
        if keyword in lower:
            return {"raw": location_raw, "city": city, "region": None, "country": country, "confidence": 0.6}

    # ── 7b. UK known-town fallback (bare town name → UK) ───────────────
    # Reed, Adzuna and some direct-ATS feeds routinely provide a bare
    # UK town/borough name with no country marker ("Heywood", "Egham",
    # "Lisvane"). Without this lookup those rows fell through to
    # country=None even though they're unambiguously UK. Curated set —
    # only entries that (a) appeared as raw_location in our DB and
    # (b) aren't ambiguous with non-UK places of the same name.
    # Recovers ~1.5K rows (2026-04-22 audit).
    _first_part_lower = lower.split(',')[0].strip()
    if _first_part_lower in _UK_KNOWN_TOWNS:
        town_display = cleaned.split(',')[0].strip()
        return {"raw": location_raw, "city": town_display, "region": None, "country": "UK", "confidence": 0.75}

    # ── 8. Unresolved ────────────────────────────────────────────────────
    return {"raw": location_raw, "city": None, "region": None, "country": None, "confidence": 0.1}


def is_allowed_country(country: str | None) -> bool:
    """Return True if the country is in the target region list, or unresolved."""
    if not country:
        return True
    return country in ALLOWED_COUNTRIES
