from __future__ import annotations

import re

from vacancysoft.classifiers.taxonomy import _TITLE_BLOCKLIST

# ---------------------------------------------------------------------------
# All taxonomy keywords — used both for relevance scoring AND as a gate
# to filter out jobs that have nothing to do with target markets.
# Ported from the proven filters.py TAXONOMY keyword lists.
# ---------------------------------------------------------------------------

# Phrases scored highest — unambiguously relevant job titles
HIGH_RELEVANCE_PHRASES: list[str] = [
    # ── Risk (broad) ──
    "risk manager", "risk officer", "risk analyst", "risk director", "head of risk",
    "chief risk", "risk management", "risk reporting", "risk analytics", "risk controls",
    "credit quality assurance", "market risk assurance",
    "risk assessment", "risk advisory", "risk assurance", "risk consultant",
    "risk governance", "risk oversight", "risk framework", "risk appetite",
    "risk monitoring", "risk mitigation", "risk strategy", "risk transformation",
    "risk operations", "risk technology", "risk data", "risk modelling", "risk modeling",
    "risk quantification", "risk infrastructure", "risk policy",
    "financial risk", "regulatory risk", "enterprise risk", "systemic risk",
    "emerging risk", "conduct risk", "reputational risk", "concentration risk",
    "third party risk", "vendor risk", "outsourcing risk", "supply chain risk",
    "climate risk", "esg risk", "sustainability risk",
    # ── Credit risk ──
    "credit risk", "credit analyst", "credit officer", "credit manager", "credit director",
    "head of credit", "chief credit", "credit strategy", "credit portfolio",
    "credit assessment", "credit review", "credit approval", "credit decision",
    "credit underwriting", "credit control", "credit monitoring", "credit exposure",
    "credit limit", "credit scoring", "credit modelling", "credit modeling",
    "credit acquisition", "credit origination", "credit structuring",
    "credit research", "credit rating", "credit surveillance",
    "credit operations", "credit administration", "credit policy",
    "wholesale credit", "retail credit", "commercial credit", "corporate credit",
    "leveraged credit", "distressed credit", "high yield credit",
    "counterparty credit", "counterparty risk", "ccr ",
    "expected loss", "loss given default", "probability of default",
    "exposure at default", "credit var", "credit migration",
    "ifrs 9", "ifrs9", "cecl", "ecl ", "expected credit loss",
    "impairment", "provision", "loan loss",
    # ── Market risk ──
    "market risk", "traded risk", "trading risk", "non traded risk",
    "interest rate risk", "irr ", "irrbb", "currency risk", "fx risk",
    "equity risk", "commodity risk", "inflation risk", "basis risk",
    "spread risk", "correlation risk",
    "var ", "value at risk", "expected shortfall", "stressed var",
    "p&l attribution", "pnl attribution", "back testing", "backtesting",
    "market data", "risk factor", "sensitivity analysis",
    "greeks", "delta", "gamma", "vega",
    # ── Liquidity risk ──
    "liquidity risk", "funding risk", "liquidity management", "liquidity reporting",
    "liquidity stress", "liquidity buffer", "liquidity coverage",
    "lcr ", "nsfr", "hqla", "contingency funding",
    "cash flow forecast", "intraday liquidity",
    # ── Operational risk ──
    "operational risk", "op risk", "oprisk",
    "operational resilience", "business continuity", "disaster recovery",
    "incident management", "loss event", "risk event",
    "key risk indicator", "kri ", "risk control self assessment", "rcsa",
    "scenario analysis", "bow tie analysis",
    # ── Model risk ──
    "model risk", "model validation", "model review", "model governance",
    "model development", "model performance", "model monitoring",
    "validation analyst", "model audit",
    # ── Prudential / capital ──
    "prudential risk", "prudential regulation", "capital management",
    "capital planning", "capital adequacy", "capital requirements",
    "capital optimisation", "capital optimization",
    "rwa ", "risk weighted", "pillar 1", "pillar 2", "pillar 3",
    "icaap", "ilaap", "srep", "stress test", "stress testing",
    "basel", "crd ", "crr ",
    "solvency", "solvency ii", "solvency 2",
    "recovery planning", "resolution planning", "living will",
    # ── Financial crime / AML / KYC ──
    "financial crime", "fincrime", "anti money laundering", "know your customer",
    "transaction monitoring", "fraud risk", "fraud analyst", "fraud manager",
    "fraud investigation", "fraud prevention", "fraud detection",
    "suspicious activity", "sar ", "sanctions screening", "sanctions analyst",
    "pep screening", "adverse media", "customer due diligence", "cdd ",
    "enhanced due diligence", "edd ", "client onboarding",
    "aml analyst", "aml officer", "aml manager", "aml director",
    "kyc analyst", "kyc officer", "kyc manager",
    "aml compliance", "sanctions compliance", "kyc compliance",
    "financial crime compliance", "economic crime",
    # ── Treasury / ALM ──
    "treasury analyst", "treasury manager", "treasury director", "head of treasury",
    "treasury operations", "treasury risk", "treasury strategy",
    "asset liability", "alm ", "balance sheet management",
    "funding strategy", "debt issuance", "capital markets funding",
    "collateral management", "margin management",
    # ── Quant / Strats ──
    "quantitative researcher", "quantitative analyst", "quantitative developer",
    "quantitative trader", "quantitative engineer", "quantitative modeler",
    "quantitative modeller", "quantitative strategist",
    "quant researcher", "quant analyst", "quant developer", "quant trader",
    "quant engineer", "quant dev", "quant strat",
    "financial engineer", "front office developer",
    "systematic trader", "algo trader", "algorithmic trader",
    # Bare data science / ML / AI engineer removed — handled by finance-context phrases below
    "deep learning", "nlp engineer",
    "pricing model", "pricing quant", "derivatives pricing", "exotic pricing",
    "structured products", "structured finance", "structurer",
    "vol surface", "volatility surface",
    "strats", "desk strat",
    # Widened quant coverage
    "signal research", "alpha research", "alpha generation", "factor research",
    "portfolio construction", "portfolio optimisation", "portfolio optimization",
    "execution research", "transaction cost analysis",
    "systematic trading", "algorithmic trading",
    "algo developer", "algo engineer", "low latency algo",
    "electronic trading developer", "etrading developer", "low latency developer",
    "quant data engineer", "trading data engineer",
    "quant data scientist", "quantitative data scientist", "financial data scientist",
    "trading data scientist",
    "machine learning quant", "ml quant", "quant ml",
    "trading ml engineer", "financial ml engineer", "alpha ml",
    "ai quant", "quant ai engineer", "trading ai engineer", "financial ai engineer",
    "ai engineer trading", "ai engineer quant",
    "ai architect trading", "ai architect quant",
    "ai engineer hedge fund", "ai engineer investment",
    "quantitative modelling", "quantitative modeling",
    "derivatives", "derivatives modelling", "derivatives modeling",
    "derivatives structuring", "derivatives analyst", "derivatives sales",
    "risk quant", "quantitative risk",
    # ── Compliance ──
    "compliance officer", "compliance manager", "compliance analyst",
    "compliance advisor", "compliance director", "head of compliance", "chief compliance",
    "compliance monitoring", "compliance assurance", "compliance testing",
    "compliance operations", "compliance risk", "compliance framework",
    "regulatory compliance", "regulatory affairs", "regulatory reporting",
    "regulatory change", "regulatory policy",
    "corporate governance", "governance risk compliance", "grc ",
    # ── Audit ──
    # External audit intentionally excluded — not a market we recruit for.
    # Blocklisted in taxonomy.py so it doesn't slip through via "audit" alone.
    "internal audit", "internal auditor",
    "statutory audit", "it audit", "it auditor", "technology audit",
    "systems audit", "cyber audit", "financial audit",
    "audit manager", "audit director", "head of audit", "chief audit",
    "audit assurance",
    # ── Cyber / InfoSec ──
    "cyber security", "cybersecurity", "information security",
    "security engineer", "security engineering", "security architect",
    "security architecture", "application security", "cloud security",
    "penetration test", "pentest", "red team", "offensive security",
    "ethical hack", "soc analyst", "security operations",
    "incident response", "detection engineer",
    "threat intelligence", "threat hunting", "threat detect",
    "cyber grc", "information security governance",
    "security compliance", "security risk", "information risk",
    "identity access", "iam ", "privileged access",
    "data protection", "data loss prevention",
    # ── Legal ──
    "legal counsel", "general counsel", "associate counsel",
    "deputy general counsel", "in-house counsel",
    "solicitor", "paralegal", "legal assistant",
    "lawyer", "attorney", "barrister",
    "contract manager", "contracts manager",
    "legal risk", "litigation", "disputes",
    # ── Front Office / Markets ──
    "portfolio manager", "fund manager", "investment manager", "asset manager",
    "portfolio analyst", "portfolio construction",
    "fixed income trad", "bond trad", "equities trad", "equity trad",
    "fx trad", "foreign exchange trad", "currency trad",
    "commodities trad", "commodity trad", "energy trad",
    "credit trad", "rates trad", "interest rate trad",
    "electronic trad", "algo trad", "execution trad",
    "sales trad", "institutional sales",
    "market maker", "market making",
    "desk head", "front office",
    "swaps trad", "distressed debt",
    "oil trad", "gas trad", "power trad", "metals trad", "emissions trad",
    "fx dealer", "fx spot", "fx forward",
    "prime broker", "prime services", "securities lending",
    "repo trad", "collateral trad",
    "equity research", "credit research", "fixed income research",
    "buy side", "sell side",
    # ── Product control / Valuation ──
    "product control", "product controller", "valuation control",
    "independent price verification", "ipv ",
    "fair value", "mark to market", "financial control",
    # ── Insurance specific ──
    # Actuarial titles intentionally removed — handled by TITLE_BLOCKLIST in taxonomy.py
    "catastrophe model", "cat model", "nat cat",
    "loss adjust", "claims manager", "claims analyst",
    "reinsurance", "treaty", "facultative",
    "underwriting manager", "underwriting analyst", "chief underwriter",
    "insurance risk", "insurance capital",
]

# Single keywords — relevant but could appear in non-target roles.
# These still count as "relevant" for the title gate filter.
MEDIUM_RELEVANCE_WORDS: list[str] = [
    "risk", "quant", "quantitative", "compliance", "audit", "auditor", "cyber",
    "legal", "counsel", "trader", "trading", "derivatives", "structuring",
    "surveillance", "governance", "resilience", "treasury",
    # "pricing" removed from medium-relevance — too broad (insurance/ecom/retail FPs)
    # "actuary"/"actuarial" removed — blocklisted in taxonomy.py
    "underwriter",
    "credit", "prudential", "xva", "cva",
    "aml", "kyc", "sanctions",
    "infosec", "ciso", "appsec", "devsecops", "siem",
    "volatility", "greeks", "exotic",
    "portfolio", "investment",
    "assurance", "regulatory",
    "smcr", "mifid", "dodd frank", "emir",
    "icaap", "ilaap",
    # Product control / finance operations
    "product control", "product controller", "financial control",
    "valuation control", "p&l", "pnl",
    # Front office / markets
    "equities", "equity", "fixed income", "rates",
    "commodities", "fx ", "foreign exchange",
    "financing", "prime broker", "prime services",
    "securities", "capital markets", "debt capital",
    "equity capital", "loan", "lending", "mortgage",
    "wealth management", "private bank", "private wealth",
    "asset management",
    # Models / quant-adjacent
    "model", "modelling", "modeling", "validation",
    # Data / analytics
    "data analyst", "data engineer", "data science",
    "analytics", "business intelligence",
    # Technology in finance
    "fintech", "regtech",
    # Corporate / banking
    "corporate bank", "transaction bank", "payment",
    "cash management", "trade finance", "correspondent bank",
    # Insurance
    "claims", "underwriting", "reinsurance", "reserving",
    "loss adjust", "catastrophe", "solvency",
    # Broad finance
    "financial analyst", "finance analyst", "finance manager",
    "financial reporting", "financial planning",
    "accountant", "accounting", "controller",
    "tax ", "taxation",
]

# Flattened set of all keywords for the is_relevant_title gate
_ALL_KEYWORDS: tuple[str, ...] = tuple(
    kw.lower() for kw in HIGH_RELEVANCE_PHRASES + MEDIUM_RELEVANCE_WORDS
)

# Word-boundary pattern cache
_PHRASE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in HIGH_RELEVANCE_PHRASES
]
_WORD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE) for word in MEDIUM_RELEVANCE_WORDS
]


def title_relevance(title: str | None) -> float:
    if not title:
        return 0.0

    # Blocklisted titles (retail, actuarial, external audit, etc.) never score
    # above the "no match" floor, even when they contain taxonomy keywords.
    if _TITLE_BLOCKLIST.search(title):
        return 0.15

    # Check high-relevance phrases first
    for pattern in _PHRASE_PATTERNS:
        if pattern.search(title):
            return 0.95

    # Then single keywords with word boundaries
    for pattern in _WORD_PATTERNS:
        if pattern.search(title):
            return 0.80

    return 0.15


def is_relevant_title(title: str | None) -> bool:
    """Return True if the title matches any taxonomy keyword.
    Used as a gate to filter out jobs that are clearly outside target markets."""
    if not title:
        return False
    if _TITLE_BLOCKLIST.search(title):
        return False
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower().strip())
    norm = re.sub(r"\s+", " ", norm).strip()
    return any(kw in norm for kw in _ALL_KEYWORDS)
