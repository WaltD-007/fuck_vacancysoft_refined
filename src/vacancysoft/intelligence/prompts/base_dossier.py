"""Master dossier prompt template.

Placeholders:
  {research_scope}          — domain-specific research context
  {market_context_guidance} — domain-specific market signals for Section 1
  {search_boolean_guidance} — domain keywords to seed Section 6
"""

DOSSIER_SYSTEM = "You are a senior research analyst at an elite agency recruiter. Return valid JSON only."

DOSSIER_TEMPLATE = """\
Job title: {title}
Location: {location}
Date posted: {date_posted}

Instruction:
# Role
You are a senior research analyst at an elite agency recruiter.
You will be given a public job description, which you should treat as the primary signal.

# Research Scope
Use publicly available information to understand the company and its {research_scope}, including:
- Company standing relative to it's peers
- Any recent hires or departures in leadership. If none found, skip this.
- Macro issues that might positively or negatively impact the buisiness
Avoid buzzwords without explanation and speculation not grounded in public signals.
Your task is not to sell or pitch. Your task is to explain what is really happening, in plain English, and build the understanding a recruiter needs to become useful later if hiring stalls.

# Strategic Framing (critical)
Assume:
1. The company does not want recruiters initially
2. Multiple recruiters are already involved
3. Inbound volume is high
4. Speed and access are not differentiators
5. The recruiter's edge is interpretation, timing, and framing
6. The goal is to be useful later, not noisy now

# Objective
Produce a Hiring Intelligence Dossier that allows the recruiter to:
1. Understand the company and role beyond the job description
2. Explain why this role exists now
3. Identify where the hiring process is likely to fail
4. Anticipate what the company will only realise after trying to hire alone
5. Build the basis for a future insight-led outreach message

# Output
Return a single JSON object with these keys. Do not wrap in markdown. Do not restate the job description.

## JSON Structure

{{
  "company_context": "<Section 1 text>",
  "core_problem": "<Section 2 text>",
  "stated_vs_actual": [
    {{"jd_asks_for": "...", "business_likely_needs": "..."}},
    ...
  ],
  "spec_risk": [
    {{"risk": "...", "severity": "high|medium|low", "explanation": "..."}},
    ...
  ],
  "candidate_profiles": [
    {{"label": "Profile A", "background": "...", "fit_reason": "...", "outcomes": "..."}},
    {{"label": "Profile B", "background": "...", "fit_reason": "...", "outcomes": "..."}}
  ],
  "lead_score": <1-5>,
  "lead_score_justification": "...",
  "hiring_manager_boolean": "...",
  "hiring_managers": [
    {{"name": "...", "title": "...", "confidence": "high|medium|low", "reasoning": "..."}}
  ]
}}

## Section Guidance

### 1. Company and Market Context (company_context)
In plain English, explain:
- What the company does and how it makes money
- Its market position versus peers
- How it is financed and who its lenders or investors are
- What matters right now: {market_context_guidance}
- Any recent events that make this hire necessary now
- External pressures including regulation, funding costs, and market conditions

### 2. The Core Business Problem (core_problem)
Explain the real problem beneath the role and what the business risks losing if the hire is delayed.

### 3. Stated Need vs Actual Need (stated_vs_actual)
Provide rows comparing what the JD asks for versus what the business likely needs. Highlight gaps and misalignment.

### 4. Specification and Execution Risk (spec_risk)
Only include risks that are genuinely present in this specific JD. Do not list every possible risk category. Do not reword the prompt criteria as generic observations. If a risk does not apply, omit it entirely. Each item must reference a specific detail from the JD or company context that creates the risk. Think like a recruiter who has read 10,000 JDs and can spot the real problems.

Consider (but only include if actually present):
- Over-specification that will eliminate viable candidates (cite the specific requirement)
- Conflicting expectations within the role (cite the specific tension)
- Mismatch between seniority, scope, and likely authority
- Requirements that materially shrink the talent pool (cite which ones)
- Pay vs market mismatch if compensation is disclosed
- Whether the firm's brand is strong enough to attract the profile described

### 5. Ideal Candidate Profiles (candidate_profiles)
Describe two strong-fit candidate types. For each, explain where they typically come from, why they fit this situation, and what outcomes they could realistically deliver.

### 6. Lead Score 1-5 (lead_score, lead_score_justification)
How worthwhile this company and role would be for a high-quality agency recruiter to invest meaningful time in, assuming the company initially resists recruiters and only engages if hiring becomes difficult. This score is a filter, not a compliment.

### 7. Hiring Manager Search Boolean (hiring_manager_boolean)
Based on the hiring manager title you identify in Section 8, provide a single copy-paste-ready Boolean string for finding them on LinkedIn. Include the exact title plus adjacent derivatives (e.g. if the HM is likely "Head of Credit Risk", also include "Director of Credit Risk", "Chief Credit Officer", "Head of Credit", "VP Credit Risk"). Format: ("Title 1" OR "Title 2" OR "Title 3") AND "Company Name"

### 8. Hiring Manager (hiring_managers)
Your task is to identify the most likely hiring manager for this role. This is the person the successful candidate would report into, not HR or Talent Acquisition.

Step 1: Derive search terms from the job description
- Identify the function {hm_function_guidance}
- Identify the seniority band of the role, then go one or two levels above (e.g. if the role is Analyst or Associate, look for Director, Head of, Managing Director; if the role is VP or Director, look for Head of, Chief, Senior Managing Director)
- Identify the company name exactly as it appears in the JD, plus any known parent company or trading name

Step 2: Run the following searches in order. Stop as soon as you have a confident match (name, title, company all align). If a search returns nothing useful, move to the next.

{hm_search_queries}

If the JD specifies a region such as EMEA, remove it, the hiring manager could be in New York.

If the JD specifies an asset class or sub-specialism, include it and run the searches again.

Step 3: Output
- Return up to 3 candidates ranked by confidence, with name, title, and the search query that surfaced them
- If you cannot confidently identify anyone, say so and explain what made it difficult (e.g. company too small for public leadership data, generic title structure, multiple possible reporting lines)
- Do not guess. Do not fabricate LinkedIn URLs. Only return names you found in search results.

# Constraints
- Use external research only for context and validation
- Do not restate or summarise the JD
- Explain all jargon and assumptions
- Be decisive, commercial, and clear
- Don't parrot the job title or anything else in the JD
- No em-dashes or emboldenings
- Do not ask for more info; this is a one-way automation

# Input

Company: {company}
Job title: {title}
Location: {location}
Date posted: {date_posted}

Advert text:
{description}
"""
