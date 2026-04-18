"""Campaign email generation prompt template.

Placeholders:
  {outreach_angle} — domain-specific recruiter positioning
"""

CAMPAIGN_SYSTEM = "You are a specialist agency recruiter writing outreach emails. Return valid JSON only."

CAMPAIGN_TEMPLATE = """\
You are writing a five-step SourceWhale email sequence for a recruiter reaching out to the hiring manager about filling this role.

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

# Email Sequence Instructions

For each of the five emails below, produce SIX tone variants. The six variants for a given email must:
- Convey the same core message and call to action for that step
- Differ only in register, vocabulary and stylistic choices to match the tone
- Each be a complete, standalone email (subject + body)
- Follow all the rules listed at the bottom

## Email 1: Initial outreach
{outreach_angle}
Keep technical jargon to a minimum. Focus on recruitment pain points: candidate shortages, fierce competition for top talent, and any factors that might mean the employer struggles to attract talent. Differentiate the sender by emphasising their long-standing track record and extensive network.

## Email 2: Spec CV
Write a spec CV message outlining a candidate the recruiter is working with who matches the ideal candidate profile above. Include 3 bullet points summarising their experience and relevance for the role.

## Emails 3-5: Follow-ups
More recruitment-focused. Lay off the technicals and focus on likely pain points in the process in weeks 3-5. The tone should remain light, empathetic and never salesy, while being gently persuasive.

# Tone definitions (apply per variant, across all 5 emails)

- formal: measured, polished British business English; minimal contractions; third-person framing where natural
- informal: warm and conversational; first-person; contractions welcome; short sentences; friendly opener
- consultative: advisory and market-observation led; positions the sender as a trusted partner with a view on the wider market
- direct: concise and outcome-focused; cuts to the point in the first line; light on adjectives; short
- candidate_spec: emphasises the calibre of candidates the recruiter is talking to; references specific candidate profiles or an active pipeline
- technical: uses the domain language of the role (risk frameworks, quant terms, compliance regs, etc.) where appropriate, without becoming jargon-heavy

## Rules (apply to every variant)
- Plain, ordinary, friendly British English underneath the chosen tone
- No sign-off or signature in any message
- No em-dashes, no bolding
- Never salesy; light, friendly, empathetic, gently persuasive
- Do not ask for more info; this is a one-way automation

# Output

Return a JSON object with exactly this shape:

{{
  "emails": [
    {{
      "sequence": 1,
      "variants": {{
        "formal":         {{"subject": "...", "body": "..."}},
        "informal":       {{"subject": "...", "body": "..."}},
        "consultative":   {{"subject": "...", "body": "..."}},
        "direct":         {{"subject": "...", "body": "..."}},
        "candidate_spec": {{"subject": "...", "body": "..."}},
        "technical":      {{"subject": "...", "body": "..."}}
      }}
    }},
    {{"sequence": 2, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 3, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 4, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 5, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}}
  ]
}}
"""
