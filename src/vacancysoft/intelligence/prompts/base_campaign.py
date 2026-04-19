"""Campaign email generation prompt template.

Placeholders:
  {outreach_angle} — domain-specific recruiter positioning
"""

CAMPAIGN_SYSTEM = "You are a specialist agency recruiter writing outreach emails. Return valid JSON only."

CAMPAIGN_TEMPLATE = """\
You are writing a five-step email sequence for a recruiter reaching out to the hiring manager about filling this role.

# Context

Company: {company}
Role: {title}
Location: {location}

## Intelligence Dossier Summary (use this to inform your messaging)

### Company Context
{company_context}

### Core Business Problem
{core_problem}

### Ideal Candidate Profile
{candidate_profile_summary}

### Specification Risk
{spec_risk_summary}

# Task

You will return EXACTLY FIVE emails in a JSON array. The array MUST contain exactly five objects. No more, no fewer.

Each of the five emails has a DIFFERENT purpose AND a DIFFERENT tone. Do not write alternative versions of the same email. Do not skip any sequence. Each `variants` object contains exactly ONE tone key — no alternatives.

## The five emails

### Email 1 — sequence=1, tone=formal
Purpose: Initial outreach.
Tone rules: Measured, polished British business English. Minimal contractions. Third-person framing where natural.
Content: {outreach_angle} Keep technical jargon minimal. Lead on recruitment pain points the employer faces: candidate shortages, competition for senior talent, difficulty attracting the specific profile the JD describes. Differentiate the sender by their track record and network — do not boast.

### Email 2 — sequence=2, tone=candidate_spec
Purpose: Spec CV introducing a specific candidate the recruiter is working with.
Tone rules: Concrete, evidence-led, warm. Refer to a real-sounding candidate profile drawn from the "Ideal Candidate Profile" section above.
Content: Body MUST include exactly 3 bullet points summarising the candidate: their recent experience, relevant skill areas, and why they fit THIS role specifically.

### Email 3 — sequence=3, tone=technical
Purpose: Follow-up that signals domain understanding.
Tone rules: Uses the domain language of the role (risk frameworks, quant terms, compliance regs, etc.) where appropriate, without becoming jargon-heavy.
Content: Reference ONE specific technical angle from the dossier's Core Business Problem or Specification Risk section. Do not list multiple angles — pick one and speak to it directly.

### Email 4 — sequence=4, tone=consultative
Purpose: Market observation positioning the sender as a trusted adviser.
Tone rules: Advisory, market-observation led. Third-person-ish framing; not salesy.
Content: Share ONE observation about how comparable firms are approaching similar hires, or a trend the hiring manager is likely already noticing.

### Email 5 — sequence=5, tone=informal
Purpose: Re-engagement with a fresh angle.
Tone rules: Warm and conversational. First-person. Contractions welcome. Short sentences. Friendly opener.
Content: Reference a different candidate profile or a different framing of the problem — signal the sender is still in the market, not nagging.

# Global rules (apply to every email)
- Plain, ordinary British English underneath the chosen tone
- No sign-off or signature
- No em-dashes, no bolding
- Never salesy; light, empathetic, gently persuasive
- Do not ask the reader for more info — this is one-way automation

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

Before returning, verify the `emails` array has exactly 5 elements and each uses the correct `sequence` number and tone key from the list above.
"""
