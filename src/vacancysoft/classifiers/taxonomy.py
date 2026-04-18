from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TaxonomyMatch:
    primary_taxonomy_key: str | None
    secondary_taxonomy_keys: list[str]
    confidence: float


# Each taxonomy key maps to a list of (pattern, weight) tuples.
# Phrases are checked before single words. Higher weight = stronger signal.
# Ported from the proven filters.py TAXONOMY with weighted scoring added.
_TAXONOMY_RULES: dict[str, list[tuple[str, float]]] = {
    "risk": [
        # Senior / cross-cutting (route via other keywords in the title,
        # fall back to Risk Management default).
        ("chief risk", 1.0), ("cro ", 0.9),
        # Credit Risk
        ("counterparty credit", 1.0), ("counterparty risk", 1.0),
        ("credit quality assurance", 1.0), ("credit risk", 1.0),
        ("credit quality", 0.9), ("retail credit risk", 0.9),
        ("risk appetite", 0.9), ("wholesale credit", 0.9),
        ("asset liability", 0.85), ("credit analyst", 0.85),
        ("credit assessment", 0.85), ("credit officer", 0.85),
        ("credit portfolio", 0.85), ("credit strategy", 0.85),
        ("credit portolio management", 0.6),
        # Market Risk
        ("market risk", 1.0), ("market risk assurance", 1.0),
        ("value at risk", 1.0),
        ("investment risk", 0.9), ("trading risk", 0.9),
        ("cva", 0.8), ("xva", 0.8),
        # Quant Risk — quant-flavoured risk roles route to Risk so the Risk
        # team owns model risk, validation, and risk-analytics quant work.
        # Phrases below at weight 1.0 must beat Quant's catch-all "quant" 0.8.
        ("model risk", 1.0), ("model risk quant", 1.0),
        ("model review", 0.9), ("model validation", 0.9),
        ("model validation quant", 1.0), ("quantitative model risk", 1.0),
        ("quantitative validation", 1.0),
        ("risk quant", 1.0), ("risk analytics quant", 1.0),
        ("quantitative risk", 1.0),
        ("reverse stress", 0.9), ("risk analytics", 0.9),
        ("stress test", 0.9), ("stress testing", 0.9),
        ("scenario analysis", 0.85), ("validation analyst", 0.85),
        ("validation engineer", 0.85),
        # Prudential Risk
        ("liquidity risk", 1.0),
        ("capital planning risk", 0.9), ("financial risk", 0.9),
        ("icaap", 0.9), ("ilaap", 0.9), ("prudential risk", 0.9),
        ("reserving risk", 0.9), ("treasury risk", 0.9),
        ("treasury analyst", 0.85), ("treasury manager", 0.85),
        ("liquidity management", 0.8),
        ("prudential", 0.7),
        ("alm", 0.6), ("treasury", 0.6),
        # Operational Risk
        ("enterprise risk", 1.0), ("head of risk", 1.0),
        ("non financial risk", 1.0), ("op risk", 1.0),
        ("operational risk", 1.0), ("ops risk", 1.0),
        ("risk controls", 0.9), ("risk framework", 0.9),
        ("risk governance", 0.9), ("risk reporting", 0.9),
        # Risk Management (catch-all, lowest specificity last)
        ("insurance risk", 1.0), ("risk management", 1.0),
        ("risk advisory", 0.9), ("risk assessment", 0.9),
        ("risk associate", 0.9), ("risk assurance", 0.9),
        ("risk consultant", 0.9),
        ("risk", 0.7),
    ],
    "quant": [
        ("quantitative research", 1.0), ("quant research", 1.0),
        ("research scientist", 0.7), ("applied scientist", 0.7),
        ("quant researcher", 1.0),
        ("quantitative developer", 1.0), ("quant developer", 1.0), ("quant dev", 1.0),
        ("front office developer", 0.8), ("quantitative engineer", 1.0),
        ("quant engineer", 1.0), ("financial engineer", 0.85),
        ("quantitative trader", 1.0), ("quant trader", 1.0),
        ("systematic trader", 0.9), ("algo trader", 0.9), ("algorithmic trader", 0.9),
        ("algo developer", 0.9), ("algo engineer", 0.85), ("low latency algo", 0.9),
        ("derivatives analyst", 0.8), ("derivatives structuring", 0.9),
        ("derivatives sales", 0.75),
        ("quantitative analyst", 1.0), ("quant analyst", 1.0),
        ("quantitative modeler", 1.0), ("quantitative modeller", 1.0),
        ("quant strat", 0.95), ("strats", 0.9), ("strat ", 0.8), ("strategist", 0.6),
        ("systematic strategy", 0.8), ("alpha", 0.5),
        # DS/ML narrowed to finance context + AI Engineer as explicit bucket
        ("quant data scientist", 0.95), ("quantitative data scientist", 0.95),
        ("trading data scientist", 0.9), ("financial data scientist", 0.85),
        ("alpha data scientist", 0.9),
        ("machine learning quant", 0.95), ("ml quant", 0.9), ("quant ml", 0.9),
        ("trading ml engineer", 0.9), ("financial ml engineer", 0.85), ("alpha ml", 0.9),
        ("ai quant", 0.9), ("quant ai engineer", 0.95),
        ("trading ai engineer", 0.9), ("financial ai engineer", 0.85),
        ("ai engineer trading", 0.9), ("ai engineer quant", 0.9),
        ("ai architect trading", 0.9), ("ai architect quant", 0.9),
        ("ai engineer hedge fund", 0.9), ("ai engineer investment", 0.85),
        ("applied ml", 0.7), ("nlp engineer", 0.7),
        ("deep learning", 0.7),
        # Note: "model validation quant", "quantitative validation",
        # "model risk quant", "quantitative model risk" intentionally NOT
        # routed here — they all belong to Risk → Quant Risk. See the
        # Quant Risk section in the "risk" rules above.
        ("pricing model", 0.8), ("pricing quant", 0.9),
        ("derivatives pricing", 0.9), ("exotic pricing", 0.9),
        ("structurer", 0.85), ("structuring", 0.75), ("structured products", 0.85),
        ("structured finance", 0.8),
        ("volatility", 0.7), ("options", 0.45), ("exotic", 0.6),
        ("vol surface", 0.9), ("greeks", 0.7),
        # Tier 3: widened coverage
        ("signal research", 0.9), ("alpha research", 0.9),
        ("alpha generation", 0.85), ("factor research", 0.85),
        ("portfolio construction", 0.8), ("portfolio optimisation", 0.8),
        ("portfolio optimization", 0.8),
        ("execution research", 0.85), ("transaction cost analysis", 0.8), ("tca", 0.6),
        ("systematic trading", 0.9), ("algorithmic trading", 0.85),
        ("electronic trading developer", 0.9), ("etrading developer", 0.9),
        ("low latency developer", 0.85),
        ("quant data engineer", 0.85), ("trading data engineer", 0.8),
        ("ml researcher trading", 0.85),
        ("quantitative modelling", 0.95), ("quantitative modeling", 0.95),
        ("derivatives modelling", 0.9), ("derivatives modeling", 0.9),
        ("structured products", 0.85),
        ("quantitative", 0.8), ("quant", 0.8),
        # NB: bare "pricing" removed — replaced with phrase-level rules above to cut false positives
        ("derivatives", 0.6),
    ],
    "compliance": [
        ("financial crime compliance", 1.0), ("aml compliance", 1.0),
        ("sanctions compliance", 1.0), ("kyc compliance", 1.0),
        ("corporate governance", 0.85), ("governance risk compliance", 0.9),
        ("financial crime", 0.95), ("anti money laundering", 0.95),
        ("know your customer", 0.95), ("sanctions compliance", 0.95),
        ("transaction monitoring", 0.95), ("fraud risk", 0.9),
        ("mlro", 1.0), ("money laundering reporting", 1.0),
        ("compliance officer", 1.0), ("compliance manager", 1.0),
        ("compliance analyst", 1.0), ("compliance advisor", 0.9),
        ("head of compliance", 1.0), ("chief compliance", 1.0),
        ("regulatory compliance", 1.0), ("conduct risk", 0.9),
        ("smcr", 0.9), ("mifid", 0.9), ("dodd frank", 0.9), ("emir", 0.8),
        ("surveillance", 0.8), ("aml", 0.9), ("kyc", 0.9),
        ("compliance", 0.8), ("governance", 0.55), ("regulatory", 0.6),
    ],
    "audit": [
        # External audit intentionally excluded — we do not recruit that market.
        # See _TITLE_BLOCKLIST for the hard exclusion.
        ("internal audit", 1.0), ("internal auditor", 1.0),
        ("it audit", 1.0), ("it auditor", 1.0), ("technology audit", 1.0),
        ("systems audit", 0.9), ("cyber audit", 0.9),
        ("audit manager", 1.0), ("audit director", 1.0), ("head of audit", 1.0),
        ("assurance", 0.6), ("audit", 0.85), ("auditor", 0.85),
    ],
    "cyber": [
        ("security engineer", 0.9), ("security engineering", 0.9),
        ("appsec", 0.9), ("application security", 0.9), ("devsecops", 0.9),
        ("cloud security engineer", 0.9),
        ("security architect", 0.9), ("security architecture", 0.9),
        ("security design", 0.85),
        ("soc analyst", 0.9), ("security operations", 0.85),
        ("incident response", 0.85), ("siem", 0.9), ("detection engineer", 0.9),
        ("threat intelligence", 0.9), ("threat hunting", 0.9), ("threat detect", 0.9),
        ("penetration test", 0.9), ("pentest", 0.9), ("red team", 0.9),
        ("offensive security", 0.9), ("ethical hack", 0.9), ("bug bounty", 0.8),
        ("cyber grc", 0.9), ("information security governance", 0.9),
        ("iso 27001", 0.85), ("nist framework", 0.85), ("nist cyber", 0.85),
        ("security compliance", 0.8), ("security risk", 0.8), ("information risk", 0.8),
        ("operational resilience", 0.9), ("business continuity", 0.85),
        ("disaster recovery", 0.8), ("crisis management", 0.75),
        ("resilience", 0.7),
        ("cyber security", 1.0), ("cybersecurity", 1.0), ("information security", 1.0),
        ("infosec", 0.9), ("security analyst", 0.85), ("security manager", 0.85),
        ("security consultant", 0.85), ("ciso", 1.0),
        ("cyber", 0.8),
    ],
    "legal": [
        ("contract manager", 0.85), ("contracts manager", 0.85), ("contract analyst", 0.85),
        ("contract negotiat", 0.8), ("procurement legal", 0.8),
        ("legal counsel", 1.0), ("general counsel", 1.0), ("associate counsel", 0.9),
        ("deputy general counsel", 1.0), ("in-house counsel", 1.0),
        ("solicitor", 0.9),
        ("paralegal", 0.8), ("legal assistant", 0.75), ("legal secretary", 0.75),
        ("lawyer", 0.9), ("attorney", 0.9), ("barrister", 0.9),
        ("legal", 0.75), ("counsel", 0.65),
    ],
    "front_office": [
        ("fixed income trad", 1.0), ("bond trad", 1.0), ("rates trad", 1.0),
        ("fixed income dealer", 0.9),
        ("equities trad", 1.0), ("equity trad", 1.0), ("stock trad", 0.9),
        ("cash equities", 0.9),
        ("fx trad", 1.0), ("foreign exchange trad", 1.0), ("currency trad", 0.9),
        ("fx dealer", 0.9), ("fx spot", 0.9), ("fx forward", 0.9),
        ("commodities trad", 1.0), ("commodity trad", 1.0), ("energy trad", 0.9),
        ("metals trad", 0.9), ("oil trad", 0.9), ("gas trad", 0.9),
        ("power trad", 0.9), ("emissions trad", 0.9),
        ("credit trad", 1.0), ("distressed debt", 0.85), ("high yield trad", 0.9),
        ("leveraged finance trad", 0.9),
        ("interest rate trad", 1.0), ("swaps trad", 0.9), ("rates desk", 0.9),
        ("market maker", 0.9), ("market making", 0.9), ("liquidity provider", 0.8),
        ("electronic trad", 0.9), ("e-trad", 0.9), ("algo trad", 0.9),
        ("low latency", 0.7), ("execution trad", 0.9),
        ("sales trad", 0.9), ("institutional sales", 0.7),
        ("portfolio manager", 1.0), ("fund manager", 0.9), ("investment manager", 0.85),
        ("asset manager", 0.8), ("portfolio analyst", 0.85),
        ("trader", 0.8), ("trading", 0.65), ("desk head", 0.8),
        ("front office", 0.7), ("portfolio", 0.5), ("investment", 0.45),
    ],
}

# ── Title blocklist: retail / non-financial roles that contain false-positive keywords ──
_TITLE_BLOCKLIST = re.compile(
    r"\b("
    r"trading assistant|trading manager.*(retail|store|shop|supermarket)"
    r"|estore trading|store trading|retail trading"
    r"|shelf stacker|stock replenish|merchandis"
    r"|checkout|cashier|store manager|shop manager|retail assistant"
    r"|customer assistant|customer team"
    r"|warehouse operative|delivery driver"
    r"|(?:senior |junior |lead |chief |head of )?actuar(?:y|ial|ies)"
    r"|(?!credit )(?:senior |junior |lead |chief |head of )?underwrit(?:er|ing)"
    r"|(?!credit )(?:senior |junior |lead )?underwriter"
    r"|(?!credit )underwriting (?:manager|analyst|assistant|specialist|technician|intern|trainee|associate|coordinator|consultant|director|operations)"
    r"|external audit|external auditor"
    r")\b",
    re.IGNORECASE,
)

# Precompile patterns: longer phrases first so they match before single words
_COMPILED_RULES: dict[str, list[tuple[re.Pattern[str], float]]] = {}
for _key, _rules in _TAXONOMY_RULES.items():
    _sorted = sorted(_rules, key=lambda r: len(r[0]), reverse=True)
    _COMPILED_RULES[_key] = [
        (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), weight)
        for phrase, weight in _sorted
    ]


def classify_against_legacy_taxonomy(title: str | None) -> TaxonomyMatch:
    if not title:
        return TaxonomyMatch(primary_taxonomy_key=None, secondary_taxonomy_keys=[], confidence=0.0)

    # Block retail / non-financial titles that contain false-positive keywords
    if _TITLE_BLOCKLIST.search(title):
        return TaxonomyMatch(primary_taxonomy_key=None, secondary_taxonomy_keys=[], confidence=0.0)

    best_key: str | None = None
    best_weight = 0.0
    secondary: list[str] = []

    for taxonomy_key, patterns in _COMPILED_RULES.items():
        key_weight = 0.0
        for pattern, weight in patterns:
            if pattern.search(title):
                key_weight = max(key_weight, weight)
                break  # take the first (longest) match per taxonomy key

        if key_weight > 0:
            if key_weight > best_weight:
                if best_key is not None:
                    secondary.append(best_key)
                best_key = taxonomy_key
                best_weight = key_weight
            else:
                secondary.append(taxonomy_key)

    confidence = round(best_weight * 0.90, 2) if best_key else 0.10
    return TaxonomyMatch(
        primary_taxonomy_key=best_key,
        secondary_taxonomy_keys=secondary,
        confidence=confidence,
    )
