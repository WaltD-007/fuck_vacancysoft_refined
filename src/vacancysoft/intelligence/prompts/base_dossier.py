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

HARD LENGTH LIMITS. Every field has a maximum word count. You MUST stay under it. If you can say it in fewer words, do. Never pad. Before returning, check every section against its limit and cut anything that exceeds.

### 1. Company and Market Context (company_context) — MAX 200 words
Cover, in order, any of these you can say something specific about:
1. What the company does and how it makes money
2. Its position versus peers
3. What matters right now: {market_context_guidance}
4. The recent event (if any) driving this hire now

If you only have material for 2 of the 4, return 2. Do not invent context to hit 4. Plain English. One paragraph.

### 2. The Core Business Problem (core_problem) — MAX 120 words
One paragraph. The real problem beneath the role and what the business risks if the hire is delayed. No preamble.

### 3. Stated Need vs Actual Need (stated_vs_actual) — EXACTLY 2 rows
Two rows only. Pick the two biggest gaps between what the JD asks for and what the business likely needs. Each of `jd_asks_for` and `business_likely_needs` is MAX 40 words.

### 4. Specification and Execution Risk (spec_risk) — 1 OR 2 items (not more)
Only real risks present in THIS JD. `explanation` is MAX 60 words (one or two sentences). Omit any risk you cannot tie to a specific JD detail.

Categories to check (include only if genuinely present in the JD):
- Over-specification that eliminates viable candidates
- Conflicting expectations within the role
- Mismatch between seniority, scope, and likely authority
- Requirements that materially shrink the talent pool
- Pay vs market mismatch if compensation is disclosed
- Brand strength vs the profile required

### 5. Ideal Candidate Profiles (candidate_profiles) — EXACTLY 2 profiles
For each profile: `background`, `fit_reason`, `outcomes` are each MAX 40 words (one or two short sentences).

### 6. Lead Score 1-5 (lead_score, lead_score_justification) — `lead_score_justification` MAX 80 words
How worthwhile this role is for a high-quality agency recruiter to invest meaningful time in, assuming the company initially resists recruiters and only engages if hiring becomes difficult. Score is a filter, not a compliment.

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
