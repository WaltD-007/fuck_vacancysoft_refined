from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TaxonomyMatch:
    primary_taxonomy_key: str | None
    secondary_taxonomy_keys: list[str]
    sub_specialism: str | None
    sub_specialism_confidence: float
    confidence: float


# Per-category default sub-specialism — used when a rule matches but carries
# no explicit sub-spec tag, and as the safety net for the catch-all rule in
# each category. Names mirror the "Sub Specialisms" tab of
# artifacts/taxonomy/prospero_categories_subspecialisms_2026-04-18.xlsx.
_CATEGORY_DEFAULT_SUB_SPEC: dict[str, str] = {
    "risk": "Risk Management",
    "quant": "Quantitative (General)",
    "compliance": "Regulatory Compliance",
    "audit": "Audit (General)",
    "cyber": "Cyber Security",
    "legal": "Legal (General)",
    "front_office": "Trading (General)",
}


# Each taxonomy key maps to a list of (pattern, weight, sub_specialism) tuples.
# Phrases are checked before single words (longer-first sort in _COMPILED_RULES).
# Higher weight = stronger signal.
# Sub-specialism strings mirror the Sub Specialisms tab of the taxonomy xlsx.
_TAXONOMY_RULES: dict[str, list[tuple[str, float, str]]] = {
    "risk": [
        # Enterprise Risk — senior / cross-cutting roles. 'chief risk' and
        # 'cro' used to be tagged 'all sub specialisms' in the xlsx sentinel
        # style; mapped to Enterprise Risk per the 2026-04-20 retag session.
        ("chief risk", 1.0, "Enterprise Risk"), ("cro ", 0.9, "Enterprise Risk"),
        # Credit Risk
        ("counterparty credit", 1.0, "Credit Risk"), ("counterparty risk", 1.0, "Credit Risk"),
        ("credit quality assurance", 1.0, "Credit Risk"), ("credit risk", 1.0, "Credit Risk"),
        ("credit quality", 0.9, "Credit Risk"), ("retail credit risk", 0.9, "Credit Risk"),
        ("wholesale credit", 0.9, "Credit Risk"),
        # 'risk appetite' and 'asset liability' routed to Credit Risk per
        # user's 2026-04-20 xlsx mapping (unusual — normally Enterprise /
        # Prudential respectively — but documented here as the user's choice).
        ("risk appetite", 0.9, "Credit Risk"),
        ("asset liability", 0.85, "Credit Risk"), ("credit analyst", 0.85, "Credit Risk"),
        ("credit assessment", 0.85, "Credit Risk"), ("credit officer", 0.85, "Credit Risk"),
        ("credit portfolio", 0.85, "Credit Risk"), ("credit strategy", 0.85, "Credit Risk"),
        ("credit portolio management", 0.6, "Credit Risk"),
        # Market Risk — per user's 2026-04-20 xlsx mapping, 'investment risk'
        # rolls up to Market Risk (removes Investment Risk's only rule).
        ("market risk", 1.0, "Market Risk"), ("market risk assurance", 1.0, "Market Risk"),
        ("value at risk", 1.0, "Market Risk"),
        ("investment risk", 0.9, "Market Risk"), ("trading risk", 0.9, "Market Risk"),
        ("cva", 0.8, "Market Risk"), ("xva", 0.8, "Market Risk"),
        # Quant Risk — quant-flavoured risk roles route to Risk so the Risk
        # team owns model risk, validation, and risk-analytics quant work.
        # Phrases below at weight 1.0 must beat Quant's catch-all "quant" 0.8.
        ("model risk", 1.0, "Quant Risk"), ("model risk quant", 1.0, "Quant Risk"),
        ("model review", 0.9, "Quant Risk"), ("model validation", 0.9, "Quant Risk"),
        ("model validation quant", 1.0, "Quant Risk"), ("quantitative model risk", 1.0, "Quant Risk"),
        ("quantitative validation", 1.0, "Quant Risk"),
        ("risk quant", 1.0, "Quant Risk"), ("risk analytics quant", 1.0, "Quant Risk"),
        ("quantitative risk", 1.0, "Quant Risk"),
        ("reverse stress", 0.9, "Quant Risk"), ("risk analytics", 0.9, "Quant Risk"),
        ("stress test", 0.9, "Quant Risk"), ("stress testing", 0.9, "Quant Risk"),
        ("scenario analysis", 0.85, "Quant Risk"), ("validation analyst", 0.85, "Quant Risk"),
        ("validation engineer", 0.85, "Quant Risk"),
        # Prudential Risk
        ("liquidity risk", 1.0, "Prudential Risk"),
        ("capital planning risk", 0.9, "Prudential Risk"), ("financial risk", 0.9, "Prudential Risk"),
        ("icaap", 0.9, "Prudential Risk"), ("ilaap", 0.9, "Prudential Risk"),
        ("prudential risk", 0.9, "Prudential Risk"),
        ("reserving risk", 0.9, "Prudential Risk"), ("treasury risk", 0.9, "Prudential Risk"),
        ("treasury analyst", 0.85, "Prudential Risk"), ("treasury manager", 0.85, "Prudential Risk"),
        ("liquidity management", 0.8, "Prudential Risk"),
        ("prudential", 0.7, "Prudential Risk"),
        ("alm", 0.6, "Prudential Risk"), ("treasury", 0.6, "Prudential Risk"),
        # Operational Risk — per user's 2026-04-20 xlsx mapping,
        # 'enterprise risk', 'head of risk', 'risk framework', 'risk governance'
        # all roll up to Operational Risk (Enterprise Risk sub-spec remains
        # populated only by 'chief risk' and 'cro').
        ("enterprise risk", 1.0, "Operational Risk"), ("head of risk", 1.0, "Operational Risk"),
        ("non financial risk", 1.0, "Operational Risk"), ("op risk", 1.0, "Operational Risk"),
        ("operational risk", 1.0, "Operational Risk"), ("ops risk", 1.0, "Operational Risk"),
        ("risk controls", 0.9, "Operational Risk"), ("risk framework", 0.9, "Operational Risk"),
        ("risk governance", 0.9, "Operational Risk"), ("risk reporting", 0.9, "Operational Risk"),
        # Third-Party Risk — operator request 2026-04-27. Was being absorbed
        # by the Risk Management catch-all; sits more naturally under
        # Operational Risk alongside vendor / outsourcing / NFR governance.
        ("third party risk", 1.0, "Operational Risk"),
        ("3rd party risk", 1.0, "Operational Risk"),
        # Risk Management (catch-all, lowest specificity last). 'risk
        # assurance' rolls up here per the 2026-04-20 retag.
        ("insurance risk", 1.0, "Risk Management"), ("risk management", 1.0, "Risk Management"),
        ("risk advisory", 0.9, "Risk Management"), ("risk assessment", 0.9, "Risk Management"),
        ("risk associate", 0.9, "Risk Management"), ("risk assurance", 0.9, "Risk Management"),
        ("risk consultant", 0.9, "Risk Management"),
        # Risk-systems engineering protection — operator clarification
        # 2026-04-27: titles whose role is "build the risk function's tooling"
        # stay in Risk regardless of any IT / Tech keyword overlap. Weight
        # ≥ 0.95 so these win against the new Cyber rules (0.9) on borderline
        # titles like "IT Risk Engineer".
        ("risk systems developer", 1.0, "Risk Management"),
        ("risk system developer", 1.0, "Risk Management"),
        ("risk technology developer", 1.0, "Risk Management"),
        ("risk software developer", 1.0, "Risk Management"),
        ("risk software engineer", 1.0, "Risk Management"),
        ("risk systems engineer", 1.0, "Risk Management"),
        ("risk system engineer", 1.0, "Risk Management"),
        ("risk technology engineer", 1.0, "Risk Management"),
        ("risk engineer", 0.95, "Risk Management"),
        ("risk developer", 0.95, "Risk Management"),
        ("risk", 0.7, "Risk Management"),
    ],
    "quant": [
        ("quantitative research", 1.0, "Quantitative Research"),
        ("quant research", 1.0, "Quantitative Research"),
        ("research scientist", 0.7, "Quantitative Research"),
        ("applied scientist", 0.7, "Data Science"),
        ("quant researcher", 1.0, "Quantitative Research"),
        ("quantitative developer", 1.0, "Quantitative Development"),
        ("quant developer", 1.0, "Quantitative Development"),
        ("quant dev", 1.0, "Quantitative Development"),
        ("front office developer", 0.8, "Quantitative Development"),
        ("quantitative engineer", 1.0, "Quantitative Development"),
        ("quant engineer", 1.0, "Quantitative Development"),
        ("financial engineer", 0.85, "Quantitative Development"),
        ("quantitative trader", 1.0, "Quantitative Trading"),
        ("quant trader", 1.0, "Quantitative Trading"),
        ("systematic trader", 0.9, "Quantitative Trading"),
        ("algo trader", 0.9, "Quantitative Trading"),
        ("algorithmic trader", 0.9, "Quantitative Trading"),
        ("algo developer", 0.9, "Quantitative Development"),
        ("algo engineer", 0.85, "Quantitative Development"),
        ("low latency algo", 0.9, "Quantitative Development"),
        ("derivatives analyst", 0.8, "Quantitative (General)"),
        ("derivatives structuring", 0.9, "Quantitative (General)"),
        ("derivatives sales", 0.75, "Quantitative Trading"),
        ("quantitative analyst", 1.0, "Quantitative (General)"),
        ("quant analyst", 1.0, "Quantitative (General)"),
        ("quantitative modeler", 1.0, "Quantitative (General)"),
        ("quantitative modeller", 1.0, "Quantitative (General)"),
        ("quant strat", 0.95, "Quantitative Strategist"),
        ("strats", 0.9, "Quantitative Strategist"),
        ("strat ", 0.8, "Quantitative Strategist"),
        ("strategist", 0.6, "Quantitative Strategist"),
        ("systematic strategy", 0.8, "Quantitative Strategist"),
        ("alpha", 0.5, "Quantitative Trading"),
        # DS / ML narrowed to finance context + AI Engineer as explicit bucket.
        # After user's April 2026 sub-spec reduction, "Machine Learning" was
        # dropped as a distinct sub-spec; ML-in-finance keywords roll up to
        # "Data Science".
        ("quant data scientist", 0.95, "Data Science"),
        ("quantitative data scientist", 0.95, "Data Science"),
        ("trading data scientist", 0.9, "Data Science"),
        ("financial data scientist", 0.85, "Data Science"),
        ("alpha data scientist", 0.9, "Data Science"),
        ("machine learning quant", 0.95, "AI Engineer"),
        ("ml quant", 0.9, "Data Science"),
        ("quant ml", 0.9, "Data Science"),
        ("trading ml engineer", 0.9, "Data Science"),
        ("financial ml engineer", 0.85, "Data Science"),
        ("alpha ml", 0.9, "Data Science"),
        ("ai quant", 0.9, "AI Engineer"),
        ("quant ai engineer", 0.95, "AI Engineer"),
        ("trading ai engineer", 0.9, "AI Engineer"),
        ("financial ai engineer", 0.85, "AI Engineer"),
        ("ai engineer trading", 0.9, "AI Engineer"),
        ("ai engineer quant", 0.9, "AI Engineer"),
        ("ai architect trading", 0.9, "AI Engineer"),
        ("ai architect quant", 0.9, "AI Engineer"),
        ("ai engineer hedge fund", 0.9, "AI Engineer"),
        ("ai engineer investment", 0.85, "AI Engineer"),
        ("applied ml", 0.7, "Data Science"),
        ("nlp engineer", 0.7, "AI Engineer"),
        ("deep learning", 0.7, "Data Science"),
        # Note: "model validation quant", "quantitative validation",
        # "model risk quant", "quantitative model risk" intentionally NOT
        # routed here — they all belong to Risk → Quant Risk. See the
        # Quant Risk section in the "risk" rules above.
        ("pricing model", 0.8, "Quantitative (General)"),
        ("pricing quant", 0.9, "Quantitative (General)"),
        ("derivatives pricing", 0.9, "Quantitative (General)"),
        ("exotic pricing", 0.9, "Quantitative (General)"),
        ("structurer", 0.85, "Quantitative (General)"),
        ("structuring", 0.75, "Quantitative (General)"),
        ("structured products", 0.85, "Quantitative (General)"),
        ("structured finance", 0.8, "Quantitative (General)"),
        ("volatility", 0.7, "Quantitative Trading"),
        ("options", 0.45, "Quantitative (General)"),
        ("exotic", 0.6, "Quantitative (General)"),
        ("vol surface", 0.9, "Quantitative Research"),
        # "greeks" intentionally stays in Quant → Quantitative (General)
        # per user's 2026-04-20 decision, even though risk-adjacent keywords
        # like model validation quant route to Risk.
        ("greeks", 0.7, "Quantitative (General)"),
        # Tier 3: widened coverage
        ("signal research", 0.9, "Quantitative Research"),
        ("alpha research", 0.9, "Quantitative Research"),
        ("alpha generation", 0.85, "Quantitative Research"),
        ("factor research", 0.85, "Quantitative Research"),
        ("portfolio construction", 0.8, "Quantitative Research"),
        ("portfolio optimisation", 0.8, "Quantitative Research"),
        ("portfolio optimization", 0.8, "Quantitative Research"),
        ("execution research", 0.85, "Quantitative Research"),
        ("transaction cost analysis", 0.8, "Quantitative Trading"),
        ("tca", 0.6, "Quantitative Trading"),
        ("systematic trading", 0.9, "Quantitative Trading"),
        ("algorithmic trading", 0.85, "Quantitative Trading"),
        ("electronic trading developer", 0.9, "Quantitative Development"),
        ("etrading developer", 0.9, "Quantitative Development"),
        ("low latency developer", 0.85, "Quantitative Development"),
        ("quant data engineer", 0.85, "Quantitative Development"),
        ("trading data engineer", 0.8, "Quantitative Development"),
        ("ml researcher trading", 0.85, "Quantitative Trading"),
        ("quantitative modelling", 0.95, "Quantitative (General)"),
        ("quantitative modeling", 0.95, "Quantitative (General)"),
        ("derivatives modelling", 0.9, "Quantitative (General)"),
        ("derivatives modeling", 0.9, "Quantitative (General)"),
        ("quantitative", 0.8, "Quantitative (General)"),
        ("quant", 0.8, "Quantitative (General)"),
        # NB: bare "pricing" removed — replaced with phrase-level rules above
        ("derivatives", 0.6, "Quantitative (General)"),
    ],
    "compliance": [
        # Per user's 2026-04-20 xlsx mapping, the *-compliance phrases
        # (sanctions compliance, kyc compliance, know your customer,
        # transaction monitoring, kyc) route to Regulatory Compliance rather
        # than Financial Crime. Pure financial-crime keywords (MLRO, AML,
        # anti-money-laundering, fraud risk, money-laundering-reporting)
        # stay in Financial Crime.
        ("financial crime compliance", 1.0, "Financial Crime"), ("aml compliance", 1.0, "Financial Crime"),
        ("sanctions compliance", 1.0, "Regulatory Compliance"), ("kyc compliance", 1.0, "Regulatory Compliance"),
        ("corporate governance", 0.85, "Governance"), ("governance risk compliance", 0.9, "Governance"),
        ("financial crime", 0.95, "Financial Crime"), ("anti money laundering", 0.95, "Financial Crime"),
        ("know your customer", 0.95, "Regulatory Compliance"),
        ("transaction monitoring", 0.95, "Regulatory Compliance"), ("fraud risk", 0.9, "Financial Crime"),
        ("mlro", 1.0, "Financial Crime"), ("money laundering reporting", 1.0, "Financial Crime"),
        ("compliance officer", 1.0, "Regulatory Compliance"), ("compliance manager", 1.0, "Regulatory Compliance"),
        ("compliance analyst", 1.0, "Regulatory Compliance"), ("compliance advisor", 0.9, "Regulatory Compliance"),
        ("head of compliance", 1.0, "Regulatory Compliance"), ("chief compliance", 1.0, "Regulatory Compliance"),
        ("regulatory compliance", 1.0, "Regulatory Compliance"),
        # Conduct Risk — promoted from a Regulatory Compliance synonym to its
        # own sub-spec on operator request 2026-04-27. Volume + commercial
        # positioning warrants distinct tagging. Longer phrases first so the
        # longest-match-first sort picks the most specific tag; "conduct risk"
        # alone is the catch-all within Conduct Risk.
        ("head of conduct risk", 1.0, "Conduct Risk"),
        ("conduct risk officer", 1.0, "Conduct Risk"),
        ("conduct risk manager", 1.0, "Conduct Risk"),
        ("conduct risk", 1.0, "Conduct Risk"),
        ("smcr", 0.9, "Regulatory Compliance"), ("mifid", 0.9, "Regulatory Compliance"),
        ("dodd frank", 0.9, "Regulatory Compliance"), ("emir", 0.8, "Regulatory Compliance"),
        ("surveillance", 0.8, "Regulatory Compliance"), ("aml", 0.9, "Financial Crime"),
        ("kyc", 0.9, "Regulatory Compliance"),
        ("compliance", 0.8, "Regulatory Compliance"), ("governance", 0.55, "Governance"),
        ("regulatory", 0.6, "Regulatory Compliance"),
    ],
    "audit": [
        # External audit intentionally excluded — we do not recruit that market.
        # See _TITLE_BLOCKLIST for the hard exclusion. Keyword still listed so
        # any leak past the blocklist still gets the right sub-spec tag.
        # Senior audit titles (manager, director, head of, auditor) now
        # route to Internal Audit per the user's 2026-04-20 xlsx mapping,
        # along with 'statutory audit' (new). 'controls testing' is new and
        # was tagged 'Assurance' in the xlsx but merged into Audit (General)
        # per Decision 3 (no Assurance sub-spec).
        ("internal audit", 1.0, "Internal Audit"), ("internal auditor", 1.0, "Internal Audit"),
        ("it audit", 1.0, "IT Audit"), ("it auditor", 1.0, "IT Audit"),
        ("technology audit", 1.0, "IT Audit"),
        ("systems audit", 0.9, "IT Audit"), ("cyber audit", 0.9, "IT Audit"),
        ("audit manager", 1.0, "Internal Audit"), ("audit director", 1.0, "Internal Audit"),
        ("head of audit", 1.0, "Internal Audit"),
        ("statutory audit", 0.9, "Internal Audit"),
        ("assurance", 0.6, "Audit (General)"), ("audit", 0.85, "Audit (General)"),
        ("auditor", 0.85, "Internal Audit"),
        ("controls testing", 0.6, "Audit (General)"),
    ],
    "cyber": [
        # 2026-04-20 retag:
        #   - Threat Detection → Threat Defence (sub-spec rename)
        #   - application security / appsec → Cyber Security (was Security Engineering)
        #   - infosec → Cyber GRC (was Cyber Security)
        ("security engineer", 0.9, "Security Engineering"), ("security engineering", 0.9, "Security Engineering"),
        ("appsec", 0.9, "Cyber Security"), ("application security", 0.9, "Cyber Security"),
        ("devsecops", 0.9, "Security Engineering"),
        ("cloud security engineer", 0.9, "Security Engineering"),
        ("security architect", 0.9, "Security Architecture"),
        ("security architecture", 0.9, "Security Architecture"),
        ("security design", 0.85, "Security Architecture"),
        ("soc analyst", 0.9, "Threat Defence"), ("security operations", 0.85, "Threat Defence"),
        ("incident response", 0.85, "Threat Defence"), ("siem", 0.9, "Threat Defence"),
        ("detection engineer", 0.9, "Threat Defence"),
        ("threat intelligence", 0.9, "Threat Defence"), ("threat hunting", 0.9, "Threat Defence"),
        ("threat detect", 0.9, "Threat Defence"),
        ("penetration test", 0.9, "Offensive Security"), ("pentest", 0.9, "Offensive Security"),
        ("red team", 0.9, "Offensive Security"),
        ("offensive security", 0.9, "Offensive Security"), ("ethical hack", 0.9, "Offensive Security"),
        ("bug bounty", 0.8, "Offensive Security"),
        ("cyber grc", 0.9, "Cyber GRC"), ("information security governance", 0.9, "Cyber GRC"),
        ("iso 27001", 0.85, "Cyber GRC"), ("nist framework", 0.85, "Cyber GRC"),
        ("nist cyber", 0.85, "Cyber GRC"),
        ("security compliance", 0.8, "Cyber GRC"), ("security risk", 0.8, "Cyber GRC"),
        ("information risk", 0.8, "Cyber GRC"),
        ("infosec", 0.9, "Cyber GRC"),
        # IT / Tech risk advisory + governance — operator request 2026-04-27.
        # Weight ≥ 0.9 to beat Risk's 0.7 catch-all decisively. Routed into the
        # existing Cyber GRC sub-spec so the Cyber structure stays unchanged.
        ("information technology risk", 1.0, "Cyber GRC"),
        ("technology risk", 0.9, "Cyber GRC"),
        ("tech risk", 0.9, "Cyber GRC"),
        ("it risk", 0.9, "Cyber GRC"),
        ("operational resilience", 0.9, "Resilience"), ("business continuity", 0.85, "Resilience"),
        ("disaster recovery", 0.8, "Resilience"), ("crisis management", 0.75, "Resilience"),
        ("resilience", 0.7, "Resilience"),
        ("cyber security", 1.0, "Cyber Security"), ("cybersecurity", 1.0, "Cyber Security"),
        ("information security", 1.0, "Cyber Security"),
        ("security analyst", 0.85, "Cyber Security"),
        ("security manager", 0.85, "Cyber Security"),
        ("security consultant", 0.85, "Cyber Security"), ("ciso", 1.0, "Cyber Security"),
        # Insider Risk / Insider Threat — operator request 2026-04-27. Routed
        # into existing Cyber Security sub-spec so the Cyber structure stays
        # unchanged. Weight 0.9 to beat Risk's 0.7 catch-all.
        ("insider threat", 0.9, "Cyber Security"),
        ("insider risk", 0.9, "Cyber Security"),
        ("cyber", 0.8, "Cyber Security"),
    ],
    "legal": [
        # 2026-04-20 retag:
        #   - Sub-spec rename: Solicitor → Lawyer (user: barristers and
        #     solicitors are both Lawyers — the right shared sub-heading).
        #   - 'procurement legal' → Legal (General) (was Contracts)
        #   - 'legal assistant' sub-spec was 'LE' in xlsx (truncation); set
        #     to Paralegal per the user's 2026-04-20 cleanup.
        ("contract manager", 0.85, "Contracts"), ("contracts manager", 0.85, "Contracts"),
        ("contract analyst", 0.85, "Contracts"),
        ("contract negotiat", 0.8, "Contracts"),
        ("procurement legal", 0.8, "Legal (General)"),
        ("legal counsel", 1.0, "Legal Counsel"), ("general counsel", 1.0, "Legal Counsel"),
        ("associate counsel", 0.9, "Legal Counsel"),
        ("deputy general counsel", 1.0, "Legal Counsel"), ("in-house counsel", 1.0, "Legal Counsel"),
        ("solicitor", 0.9, "Lawyer"),
        ("paralegal", 0.8, "Paralegal"), ("legal assistant", 0.75, "Paralegal"),
        ("legal secretary", 0.75, "Paralegal"),
        ("lawyer", 0.9, "Lawyer"), ("attorney", 0.9, "Lawyer"),
        ("barrister", 0.9, "Lawyer"),
        ("legal", 0.75, "Legal (General)"), ("counsel", 0.65, "Legal Counsel"),
    ],
    "front_office": [
        ("fixed income trad", 1.0, "Fixed Income Trading"), ("bond trad", 1.0, "Fixed Income Trading"),
        ("rates trad", 1.0, "Rates Trading"),
        ("fixed income dealer", 0.9, "Fixed Income Trading"),
        ("equities trad", 1.0, "Equities Trading"), ("equity trad", 1.0, "Equities Trading"),
        ("stock trad", 0.9, "Equities Trading"),
        ("cash equities", 0.9, "Equities Trading"),
        ("fx trad", 1.0, "FX Trading"), ("foreign exchange trad", 1.0, "FX Trading"),
        ("currency trad", 0.9, "FX Trading"),
        ("fx dealer", 0.9, "FX Trading"), ("fx spot", 0.9, "FX Trading"),
        ("fx forward", 0.9, "FX Trading"),
        ("commodities trad", 1.0, "Commodities Trading"), ("commodity trad", 1.0, "Commodities Trading"),
        ("energy trad", 0.9, "Commodities Trading"),
        ("metals trad", 0.9, "Commodities Trading"), ("oil trad", 0.9, "Commodities Trading"),
        ("gas trad", 0.9, "Commodities Trading"),
        ("power trad", 0.9, "Commodities Trading"), ("emissions trad", 0.9, "Commodities Trading"),
        ("credit trad", 1.0, "Credit Trading"), ("distressed debt", 0.85, "Credit Trading"),
        ("high yield trad", 0.9, "Credit Trading"),
        ("leveraged finance trad", 0.9, "Credit Trading"),
        ("interest rate trad", 1.0, "Rates Trading"), ("swaps trad", 0.9, "Rates Trading"),
        ("rates desk", 0.9, "Rates Trading"),
        ("market maker", 0.9, "Market Making"), ("market making", 0.9, "Market Making"),
        ("liquidity provider", 0.8, "Market Making"),
        ("electronic trad", 0.9, "Electronic Trading"), ("e-trad", 0.9, "Electronic Trading"),
        ("algo trad", 0.9, "Electronic Trading"),
        ("low latency", 0.7, "Electronic Trading"), ("execution trad", 0.9, "Electronic Trading"),
        ("sales trad", 0.9, "Sales Trading"), ("institutional sales", 0.7, "Sales Trading"),
        ("portfolio manager", 1.0, "Portfolio Management"), ("fund manager", 0.9, "Portfolio Management"),
        ("investment manager", 0.85, "Portfolio Management"),
        ("asset manager", 0.8, "Portfolio Management"), ("portfolio analyst", 0.85, "Portfolio Management"),
        ("trader", 0.8, "Trading (General)"), ("trading", 0.65, "Trading (General)"),
        ("desk head", 0.8, "Trading (General)"),
        ("front office", 0.7, "Trading (General)"), ("portfolio", 0.5, "Portfolio Management"),
        ("investment", 0.45, "Portfolio Management"),
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

# ── Out-of-scope blocklist: internships and French fixed-term schemes ──
# We don't recruit for these populations, so titles matching here have
# their primary_taxonomy_key set to None and drop out of every export.
_OUT_OF_SCOPE_BLOCKLIST = re.compile(
    "|".join([
        # Internships and student placements (English / German / French)
        r"\bintern(s|ship[s]?)?\b",
        r"\bco-?op[s]?\b",
        r"\bpraktikum\b",
        r"\bpraktikant(in)?\b",
        r"\bwerkstudium\b",
        r"\bwerkstudent(in)?\b",
        r"\bstagiaire\b",
        r"\bplacement programme?\b",
        r"\b\d+\s*-?\s*month[s]?\s+placement\b",
        # French CDD — only when followed by a fixed-term marker (digit,
        # "de", "month", "mois", "fixed"). Avoids matching CDD = Customer
        # Due Diligence, which is a permanent compliance role we DO recruit.
        r"\bcdd[\s\-]+(\d|de\b|month|mois|fixed)",
        # French V.I.E. (Volontariat International en Entreprise) —
        # formal dotted form, the full French phrase, or "VIE" followed
        # by a dash separator (the common job-title pattern).
        r"\bv\.i\.e\.",
        r"\bvolontariat\s+international\b",
        r"\bvie\b\s*[\-\u2013]",
    ]),
    re.IGNORECASE,
)

# Precompile patterns: longer phrases first so they match before single words.
# Each entry now carries (pattern, weight, sub_specialism).
_COMPILED_RULES: dict[str, list[tuple[re.Pattern[str], float, str]]] = {}
for _key, _rules in _TAXONOMY_RULES.items():
    _sorted = sorted(_rules, key=lambda r: len(r[0]), reverse=True)
    _COMPILED_RULES[_key] = [
        (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), weight, sub_spec)
        for phrase, weight, sub_spec in _sorted
    ]


def classify_against_legacy_taxonomy(title: str | None) -> TaxonomyMatch:
    if not title:
        return TaxonomyMatch(
            primary_taxonomy_key=None,
            secondary_taxonomy_keys=[],
            sub_specialism=None,
            sub_specialism_confidence=0.0,
            confidence=0.0,
        )

    # Block retail / non-financial titles that contain false-positive keywords
    if _TITLE_BLOCKLIST.search(title):
        return TaxonomyMatch(
            primary_taxonomy_key=None,
            secondary_taxonomy_keys=[],
            sub_specialism=None,
            sub_specialism_confidence=0.0,
            confidence=0.0,
        )

    # Block out-of-scope populations we don't recruit for (internships, CDD, VIE)
    if _OUT_OF_SCOPE_BLOCKLIST.search(title):
        return TaxonomyMatch(
            primary_taxonomy_key=None,
            secondary_taxonomy_keys=[],
            sub_specialism=None,
            sub_specialism_confidence=0.0,
            confidence=0.0,
        )

    best_key: str | None = None
    best_weight = 0.0
    best_sub_spec: str | None = None
    secondary: list[str] = []

    for taxonomy_key, patterns in _COMPILED_RULES.items():
        # For this category, find the strongest-weight matching rule and
        # remember its sub-specialism. Since patterns are sorted longer-first,
        # a tie breaks in favour of the longer phrase, which is what we want.
        key_weight = 0.0
        key_sub_spec: str | None = None
        for pattern, weight, sub_spec in patterns:
            if pattern.search(title):
                if weight > key_weight:
                    key_weight = weight
                    key_sub_spec = sub_spec
                break  # take the first (longest) match per taxonomy key

        if key_weight > 0:
            if key_weight > best_weight:
                if best_key is not None:
                    secondary.append(best_key)
                best_key = taxonomy_key
                best_weight = key_weight
                best_sub_spec = key_sub_spec
            else:
                secondary.append(taxonomy_key)

    # Defensive fallback: if the winning rule had no sub-spec (shouldn't
    # happen with the current table, but keeps the contract safe), use the
    # category's default sub-specialism.
    if best_key is not None and best_sub_spec is None:
        best_sub_spec = _CATEGORY_DEFAULT_SUB_SPEC.get(best_key)

    confidence = round(best_weight * 0.90, 2) if best_key else 0.10
    sub_spec_confidence = round(best_weight * 0.90, 2) if best_sub_spec else 0.0
    return TaxonomyMatch(
        primary_taxonomy_key=best_key,
        secondary_taxonomy_keys=secondary,
        sub_specialism=best_sub_spec,
        sub_specialism_confidence=sub_spec_confidence,
        confidence=confidence,
    )
