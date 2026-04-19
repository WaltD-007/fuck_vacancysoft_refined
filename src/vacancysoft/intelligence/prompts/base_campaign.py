"""Campaign email generation prompt template.

Placeholders:
  {company}                     — hiring company
  {title}                       — role title
  {location}                    — role location
  {company_context}             — dossier §1
  {core_problem}                — dossier §2
  {stated_vs_actual_summary}    — dossier §3 rendered as "JD asks X / Business needs Y"
  {spec_risk_summary}           — dossier §4 with severity + explanation
  {candidate_profile_summary}   — dossier §5 including outcomes
  {lead_score_context}          — dossier §6 justification
  {hiring_manager_line}         — name + title of highest-confidence HM
  {outreach_angle}              — domain-specific recruiter positioning
"""

CAMPAIGN_SYSTEM = "You are a specialist agency recruiter writing outreach emails. Return valid JSON only."

CAMPAIGN_TEMPLATE = """\
You are writing a five-step email sequence for a recruiter reaching out to the hiring manager about filling this role.

# Role context

Company: {company}
Role: {title}
Location: {location}
Likely hiring manager: {hiring_manager_line}

# Intelligence Dossier (draw specific, concrete details from these sections — do not paraphrase or sell)

## Company Context
{company_context}

## Core Business Problem
{core_problem}

## Stated Need vs Actual Need (gap analysis)
{stated_vs_actual_summary}

## Specification Risk (with severity + reasoning)
{spec_risk_summary}

## Ideal Candidate Profiles (with background, fit reasoning, likely outcomes)
{candidate_profile_summary}

## Why this lead is worth engaging
{lead_score_context}

# Task

You will return EXACTLY FIVE emails in a JSON array. The array MUST contain exactly five objects. No more, no fewer.

Each of the five emails has a DIFFERENT purpose AND a DIFFERENT tone. Do not write alternative versions of the same email. Do not skip any sequence. Each `variants` object contains exactly ONE tone key — no alternatives.

Every email must lean on one or more specific details from the dossier sections above. Generic outreach that could apply to any firm is a failure. Reference the company's actual situation, the actual gap, or a specific spec risk. Do not invent details that aren't in the dossier.

## The five emails

### Email 1 — sequence=1, tone=formal
Purpose: Initial outreach.
Tone rules: Measured, polished British business English. Minimal contractions. Third-person framing where natural.
Source: Lean on "Company Context" and "Core Business Problem". If a likely hiring manager is identified above, address them by title (not name). {outreach_angle}
Content: Open with one sentence that references the company's specific situation or the core problem (must feel informed, not generic). Follow with one sentence positioning the sender. Close with a low-pressure next step. Keep technical jargon minimal.

### Email 2 — sequence=2, tone=candidate_spec
Purpose: Spec CV introducing a specific candidate the recruiter is working with.
Tone rules: Concrete, evidence-led, warm.
Source: Draw the candidate directly from "Ideal Candidate Profiles" above. Name the background, fit reasoning, and likely outcomes from Profile A (if present). The bullets MUST reflect what that profile actually looks like, not a generic senior-hire archetype.
Content: One-sentence opener referencing why you're sharing this specific candidate. Body MUST include exactly 3 bullet points summarising: (1) recent experience, (2) relevant skill areas, (3) why they fit THIS role (not generic "strong candidate" language).

### Email 3 — sequence=3, tone=technical
Purpose: Follow-up that signals domain understanding.
Tone rules: Uses the domain language of the role (risk frameworks, quant terms, compliance regs, etc.) where appropriate, without becoming jargon-heavy.
Source: Pick ONE item from "Specification Risk" OR ONE tension from "Stated Need vs Actual Need". Name the specific risk/gap (e.g. "the JD asks for X, but the desk you'd sit on actually needs Y") and speak directly to it.
Content: 3–5 sentences. Cite the specific risk/gap, then offer one thought on how a good candidate approaches that tension. Do not list multiple risks; pick one.

### Email 4 — sequence=4, tone=consultative
Purpose: Market observation positioning the sender as a trusted adviser.
Tone rules: Advisory, market-observation led. Third-person-ish framing; not salesy.
Source: Reference a trend that comparable firms are seeing, tied back to the "Company Context" section above. Ground the observation — do not hand-wave about "the market" in general.
Content: 3–5 sentences. One concrete market observation + one implication for this specific hire + a light next-step.

### Email 5 — sequence=5, tone=informal
Purpose: Re-engagement with a fresh angle.
Tone rules: Warm and conversational. First-person. Contractions welcome. Short sentences. Friendly opener.
Source: Reference a different "Ideal Candidate Profile" (if two exist), or a different framing of the problem from "Core Business Problem". Signal the sender is still in the market, not nagging.
Content: Brief. 2–4 sentences. One warm opener, one fresh angle, one low-pressure line.

# Global rules (apply to every email)
- Plain, ordinary British English underneath the chosen tone
- No sign-off or signature
- No em-dashes, no bolding
- Never salesy; light, empathetic, gently persuasive
- Do not ask the reader for more info — this is one-way automation
- Do not name the hiring manager by their real name even if identified above; refer to them by title or generically ("your team", "the risk team")

# Output schema

Return this exact JSON shape. Replace "..." with real content. Do NOT add extra keys, extra tone variants, or extra sequences. The `emails` array MUST have exactly 5 elements in the order shown:

{{
  "emails": [
    {{"sequence": 1, "variants": {{"formal":         {{"subject": "...", "body": "..."}}}}}},
    {{"sequence": 2, "variants": {{"candidate_spec": {{"subject": "...", "body": "..."}}}}}},
    {{"sequence": 3, "variants": {{"technical":      {{"subject": "...", "body": "..."}}}}}},
    {{"sequence": 4, "variants": {{"consultative":   {{"subject": "...", "body": "..."}}}}}},
    {{"sequence": 5, "variants": {{"informal":       {{"subject": "...", "body": "..."}}}}}}
  ]
}}

Before returning, verify:
- the `emails` array has exactly 5 elements in the order [formal, candidate_spec, technical, consultative, informal]
- each email leans on at least one specific dossier detail (not generic prose)
- no email names the hiring manager by first name
"""
