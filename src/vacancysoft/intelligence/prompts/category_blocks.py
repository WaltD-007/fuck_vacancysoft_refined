"""Category-specific variable blocks for prompt templates.

Each category maps to the placeholders used in base_dossier.py and base_campaign.py.

──────────────────────────────────────────────────────────────────────
HM search templates — v1 vs v2
──────────────────────────────────────────────────────────────────────

Two generations coexist here, selected at runtime by
``configs/app.toml [intelligence] hm_template_version``:

- **v2 (default)**: single generic template (``_HM_SEARCHES_V2_TEMPLATE``)
  using ``[function]`` as a runtime variable filled from
  ``ClassificationResult.sub_specialism``. Added 2026-04-20 by the
  operator in ``~/Desktop/linkedin_search_strings_1.xlsx``. Covers
  every category with 7 universally-applicable title patterns.
  ``[location]`` is optional — stripped when the lead has no city or
  country set.

- **v1 (legacy, dormant)**: the original seven per-category blocks
  (``_LEGACY_<CAT>_HM_SEARCHES``) kept wired up via
  ``hm_search_queries_v1`` keys in ``CATEGORY_BLOCKS``. Rollback path:
  set ``hm_template_version = "v1"`` in ``configs/app.toml`` and
  restart the API + worker.
"""

# ── v2 — generic template used by every category ────────────────────
# Variables filled at render time by dossier.py / hm_search_serpapi.py:
#   [company name] ← EnrichedJob.team or Source.employer_name
#   [function]     ← ClassificationResult.sub_specialism
#                    (e.g. "Credit Risk", "Financial Crime",
#                    "Quantitative Trading")
#   [location]     ← EnrichedJob.location_city, fallback location_country,
#                    or stripped entirely (with its leading space) when
#                    neither is set.
_HM_SEARCHES_V2_TEMPLATE = """\
Search 1: "[company name]" "head of [function]" site:linkedin.com/in [location]
Search 2: "[company name]" "global head of [function]" site:linkedin.com/in [location]
Search 3: "[company name]" "regional head of [function]" site:linkedin.com/in [location]
Search 4: "[company name]" "director of [function]" site:linkedin.com/in [location]
Search 5: "[company name]" "[function] director" site:linkedin.com/in [location]
Search 6: "[company name]" "managing director [function]" site:linkedin.com/in [location]
Search 7: "[company name]" "chief [function] officer" site:linkedin.com/in [location]"""


# ── v1 — legacy per-category templates (rollback target) ────────────
# Kept so rolling back via `hm_template_version = "v1"` in app.toml
# continues to render the exact search strings used pre-2026-04-20.
# Do NOT edit these; if you need to tune, do it on v2 and iterate there.

_LEGACY_RISK_HM_SEARCHES = """\
Search 1: "[company name]" "head of credit" site:linkedin.com/in
Search 2: "[company name]" "head of risk" site:linkedin.com/in
Search 3: "[company name]" "chief risk officer" site:linkedin.com/in
Search 4: "[company name]" "director of risk" site:linkedin.com/in
Search 5: "[company name]" "head of market risk" site:linkedin.com/in
Search 6: "[company name]" "head of operational risk" site:linkedin.com/in
Search 7: "[company name]" "[function]" "managing director" site:linkedin.com/in"""

_LEGACY_QUANT_HM_SEARCHES = """\
Search 1: "[company name]" "head of quantitative research" site:linkedin.com/in
Search 2: "[company name]" "head of quant" site:linkedin.com/in
Search 3: "[company name]" "director of quantitative" site:linkedin.com/in
Search 4: "[company name]" "chief investment officer" site:linkedin.com/in
Search 5: "[company name]" "head of systematic" site:linkedin.com/in
Search 6: "[company name]" "head of research" site:linkedin.com/in
Search 7: "[company name]" "quantitative" "managing director" site:linkedin.com/in"""

_LEGACY_COMPLIANCE_HM_SEARCHES = """\
Search 1: "[company name]" "head of compliance" site:linkedin.com/in
Search 2: "[company name]" "chief compliance officer" site:linkedin.com/in
Search 3: "[company name]" "director of compliance" site:linkedin.com/in
Search 4: "[company name]" "head of financial crime" site:linkedin.com/in
Search 5: "[company name]" "MLRO" site:linkedin.com/in
Search 6: "[company name]" "head of regulatory" site:linkedin.com/in
Search 7: "[company name]" "compliance" "managing director" site:linkedin.com/in"""

_LEGACY_AUDIT_HM_SEARCHES = """\
Search 1: "[company name]" "head of internal audit" site:linkedin.com/in
Search 2: "[company name]" "chief audit executive" site:linkedin.com/in
Search 3: "[company name]" "director of internal audit" site:linkedin.com/in
Search 4: "[company name]" "head of audit" site:linkedin.com/in
Search 5: "[company name]" "audit" "managing director" site:linkedin.com/in
Search 6: "[company name]" "head of assurance" site:linkedin.com/in
Search 7: "[company name]" "chief risk officer" site:linkedin.com/in"""

_LEGACY_CYBER_HM_SEARCHES = """\
Search 1: "[company name]" "CISO" site:linkedin.com/in
Search 2: "[company name]" "chief information security officer" site:linkedin.com/in
Search 3: "[company name]" "head of cyber" site:linkedin.com/in
Search 4: "[company name]" "head of information security" site:linkedin.com/in
Search 5: "[company name]" "director of security" site:linkedin.com/in
Search 6: "[company name]" "head of security engineering" site:linkedin.com/in
Search 7: "[company name]" "chief technology officer" site:linkedin.com/in"""

_LEGACY_LEGAL_HM_SEARCHES = """\
Search 1: "[company name]" "general counsel" site:linkedin.com/in
Search 2: "[company name]" "head of legal" site:linkedin.com/in
Search 3: "[company name]" "chief legal officer" site:linkedin.com/in
Search 4: "[company name]" "director of legal" site:linkedin.com/in
Search 5: "[company name]" "deputy general counsel" site:linkedin.com/in
Search 6: "[company name]" "head of legal" "[function]" site:linkedin.com/in
Search 7: "[company name]" "legal" "managing director" site:linkedin.com/in"""

_LEGACY_FO_HM_SEARCHES = """\
Search 1: "[company name]" "head of [asset class/desk]" site:linkedin.com/in
Search 2: "[company name]" "desk head" "[asset class]" site:linkedin.com/in
Search 3: "[company name]" "managing director" "[asset class]" site:linkedin.com/in
Search 4: "[company name]" "head of trading" site:linkedin.com/in
Search 5: "[company name]" "chief investment officer" site:linkedin.com/in
Search 6: "[company name]" "head of markets" site:linkedin.com/in
Search 7: "[company name]" "global head" "[asset class]" site:linkedin.com/in"""


def render_hm_search_template_v2(
    template: str,
    company_name: str,
    function: str,
    location: str | None,
) -> str:
    """Render the v2 generic HM-search template with variables substituted.

    - company_name and function are always substituted.
    - location is optional: when empty/None, the trailing ' [location]'
      on each line is stripped (including the leading space) so the
      search string doesn't contain a dangling empty quoted term.

    Returned string has one Search-line per newline, matching the
    legacy v1 format so callers treat both paths identically.
    """
    company = (company_name or "").strip()
    func = (function or "").strip()
    loc = (location or "").strip()

    # Substitute the two always-present variables.
    rendered = template.replace("[company name]", company).replace("[function]", func)

    # Handle the optional location. When empty, strip both the token
    # itself and the single space that precedes it so the line ends
    # cleanly at site:linkedin.com/in.
    if loc:
        # Wrap in quotes for Google phrase matching.
        rendered = rendered.replace("[location]", f'"{loc}"')
    else:
        rendered = rendered.replace(" [location]", "").replace("[location]", "")
    return rendered


CATEGORY_BLOCKS: dict[str, dict[str, str]] = {
    "risk": {
        # Short noun phrase used by V3's persona block ({recruiter_specialism}).
        # Ignored by V1 / V2.
        "recruiter_specialism": "risk recruitment specialist",
        "research_scope": (
            "wholesale credit context across buy-side and sell-side perspectives"
        ),
        "market_context_guidance": (
            "leverage, liquidity, covenants, refinancing, capital allocation, "
            "risk governance, regulatory capital requirements, stress testing outcomes, "
            "and changes in risk appetite"
        ),
        "search_boolean_guidance": (
            "credit risk, market risk, counterparty risk, operational risk, "
            "model validation, liquidity risk, enterprise risk, stress testing, "
            "risk analytics, ICAAP, ILAAP, Basel, capital planning"
        ),
        "outreach_angle": (
            "Subtly position the sender as a specialist risk recruitment consultant "
            "with a strong network and successful track record across credit, market, "
            "and operational risk. Emphasise their long-standing track record and "
            "extensive network in risk recruitment."
        ),
        "hm_function_guidance": (
            "(e.g. Credit Risk, Counterparty Risk, Portfolio Risk, Market Risk, "
            "Operational Risk, Model Validation, Liquidity Risk)"
        ),
        "hm_search_queries": _LEGACY_RISK_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_RISK_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "quant": {
        "recruiter_specialism": "quantitative-talent specialist",
        "research_scope": (
            "quantitative strategies, model infrastructure, and research culture"
        ),
        "market_context_guidance": (
            "alpha generation, systematic strategy performance, model infrastructure "
            "investment, technology stack evolution, research team expansion, "
            "and competition for quantitative talent"
        ),
        "search_boolean_guidance": (
            "quantitative researcher, quant developer, quant trader, "
            "quantitative analyst, data scientist, machine learning engineer, "
            "systematic strategy, alpha research, model validation, pricing, "
            "derivatives, structured products, volatility"
        ),
        "outreach_angle": (
            "Subtly position the sender as a quantitative talent specialist "
            "with a proven track record placing quants across systematic funds, "
            "prop trading firms, and sell-side desks. Emphasise understanding of "
            "the difference between research, development, and trading roles."
        ),
        "hm_function_guidance": (
            "(e.g. Quantitative Research, Quantitative Trading, Systematic Strategies, "
            "Quant Development, Model Validation, Data Science)"
        ),
        "hm_search_queries": _LEGACY_QUANT_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_QUANT_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "compliance": {
        "recruiter_specialism": "compliance and financial-crime recruitment specialist",
        "research_scope": (
            "regulatory compliance landscape, enforcement trends, and financial crime frameworks"
        ),
        "market_context_guidance": (
            "FCA/PRA enforcement actions, regulatory change programmes, AML framework "
            "remediation, sanctions regime changes, consumer duty implementation, "
            "and conduct risk priorities"
        ),
        "search_boolean_guidance": (
            "compliance officer, AML, KYC, sanctions, financial crime, "
            "MLRO, conduct risk, regulatory affairs, compliance monitoring, "
            "transaction monitoring, SAR, consumer duty, FCA, PRA"
        ),
        "outreach_angle": (
            "Subtly position the sender as a compliance and financial crime "
            "recruitment specialist who understands the regulatory pressure driving "
            "hiring. Emphasise their network across banks, asset managers, and "
            "payments firms."
        ),
        "hm_function_guidance": (
            "(e.g. Compliance, Financial Crime, AML, Regulatory Affairs, "
            "Conduct Risk, Sanctions, KYC)"
        ),
        "hm_search_queries": _LEGACY_COMPLIANCE_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_COMPLIANCE_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "audit": {
        "recruiter_specialism": "internal-audit and assurance recruitment specialist",
        "research_scope": (
            "internal control environment, audit committee priorities, and assurance frameworks"
        ),
        "market_context_guidance": (
            "regulatory findings, remediation backlogs, audit committee scrutiny, "
            "SOX compliance, ICAAP/ILAAP audit coverage, technology audit capability, "
            "and the Big 4 to industry pipeline"
        ),
        "search_boolean_guidance": (
            "internal audit, IT audit, audit manager, chief audit executive, "
            "assurance, controls testing, SOX, ICAAP, operational audit, "
            "financial audit, regulatory audit, audit analytics"
        ),
        "outreach_angle": (
            "Subtly position the sender as an internal audit and assurance "
            "recruitment specialist with deep knowledge of the Big 4 to industry "
            "pipeline and audit committee expectations."
        ),
        "hm_function_guidance": (
            "(e.g. Internal Audit, IT Audit, Financial Audit, Operational Audit, "
            "Regulatory Audit, Assurance)"
        ),
        "hm_search_queries": _LEGACY_AUDIT_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_AUDIT_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "cyber": {
        "recruiter_specialism": "cyber-security recruitment specialist",
        "research_scope": (
            "cyber security posture, threat landscape, and operational resilience"
        ),
        "market_context_guidance": (
            "incident history, security maturity, DORA and NIS2 compliance, "
            "cloud security transformation, third-party risk, pen testing findings, "
            "and board-level cyber governance"
        ),
        "search_boolean_guidance": (
            "cyber security, information security, CISO, SOC analyst, "
            "penetration testing, AppSec, threat intelligence, security engineer, "
            "cloud security, operational resilience, DORA, incident response"
        ),
        "outreach_angle": (
            "Subtly position the sender as a cyber security recruitment specialist "
            "who understands the regulated financial services environment, "
            "security clearance requirements, and the scarcity of senior talent."
        ),
        "hm_function_guidance": (
            "(e.g. Cyber Security, Information Security, Security Engineering, "
            "Application Security, Threat Intelligence, SOC)"
        ),
        "hm_search_queries": _LEGACY_CYBER_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_CYBER_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "legal": {
        "recruiter_specialism": "legal recruitment specialist",
        "research_scope": (
            "legal and governance landscape, litigation exposure, and regulatory investigations"
        ),
        "market_context_guidance": (
            "litigation pipeline, regulatory investigations, M&A activity, "
            "contract complexity, jurisdictional scope, and changes in legal "
            "team structure or general counsel reporting lines"
        ),
        "search_boolean_guidance": (
            "legal counsel, general counsel, in-house lawyer, paralegal, "
            "contract manager, regulatory lawyer, litigation, M&A legal, "
            "company secretary, governance, legal operations"
        ),
        "outreach_angle": (
            "Subtly position the sender as a legal and governance recruitment "
            "specialist with a strong network across in-house legal teams and "
            "private practice, understanding the nuances of financial services law."
        ),
        "hm_function_guidance": (
            "(e.g. Legal, Structured Finance Legal, Regulatory Legal, "
            "Litigation, M&A, Company Secretarial, Governance)"
        ),
        "hm_search_queries": _LEGACY_LEGAL_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_LEGAL_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
    "front_office": {
        "recruiter_specialism": "front-office recruitment specialist",
        "research_scope": (
            "trading desk structure, market-making capabilities, and client franchise"
        ),
        "market_context_guidance": (
            "desk P&L trends, flow vs proprietary activity, client franchise strength, "
            "compensation dynamics, platform migration, electronic trading adoption, "
            "and regulatory impact on market structure"
        ),
        "search_boolean_guidance": (
            "trader, portfolio manager, market maker, sales trader, "
            "structurer, desk head, rates, equities, FX, commodities, credit, "
            "fixed income, derivatives, electronic trading"
        ),
        "outreach_angle": (
            "Subtly position the sender as a front office recruitment specialist "
            "who understands desk economics, compensation benchmarking, and the "
            "competitive dynamics of hiring revenue-generating talent."
        ),
        "hm_function_guidance": (
            "(e.g. the specific asset class or desk: Rates, Equities, FX, "
            "Commodities, Credit, Fixed Income, Derivatives)"
        ),
        "hm_search_queries": _LEGACY_FO_HM_SEARCHES,  # back-compat alias; points at v1
        "hm_search_queries_v1": _LEGACY_FO_HM_SEARCHES,
        "hm_search_queries_v2": _HM_SEARCHES_V2_TEMPLATE,
    },
}

DEFAULT_CATEGORY = "risk"
