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
You are writing a five-step email sequence for a recruiter reaching out to the hiring manager about filling this role. For each step you will produce SIX tone variants so the operator can pick the right register per send.

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

You will return EXACTLY FIVE emails in a JSON array. Each email object has a `sequence` (1-5) and a `variants` object holding SIX tone keys: formal, informal, consultative, direct, candidate_spec, technical. Every tone must be populated for every sequence. No sequence may be skipped. No tone may be left empty. 5 sequences × 6 tones = 30 {{subject, body}} pairs total.

Every variant for a given sequence must convey the SAME core message and call-to-action for that step — only the register, vocabulary and stylistic choices differ. Do not change the underlying message between tones; re-voice it.

Every email must lean on one or more specific details from the dossier sections above. Generic outreach that could apply to any firm is a failure. Reference the company's actual situation, the actual gap, or a specific spec risk. Do not invent details that aren't in the dossier.

## The five steps

### Step 1 — Initial outreach
Purpose: First contact.
Source: Lean on "Company Context" and "Core Business Problem". {outreach_angle}
Content: Open with one sentence that references the company's specific situation or the core problem (must feel informed, not generic). Position the sender. Close with a low-pressure next step. Keep technical jargon minimal across all variants.

### Step 2 — Spec CV (candidate introduction)
Purpose: Introduce a specific candidate the recruiter is working with.
Source: Draw the candidate directly from "Ideal Candidate Profiles" above. Name the background, fit reasoning, and likely outcomes from Profile A (if present).
Content: Each variant's body MUST include exactly 3 bullet points summarising: (1) recent experience, (2) relevant skill areas, (3) why they fit THIS role (not generic "strong candidate" language).

### Step 3 — Technical angle (domain follow-up)
Purpose: Follow-up that signals domain understanding.
Source: Pick ONE item from "Specification Risk" OR ONE tension from "Stated Need vs Actual Need". Name the specific risk/gap and speak directly to it.
Content: 3-5 sentences per variant. Cite the specific risk/gap, then one thought on how a good candidate approaches that tension. Do not list multiple risks; pick one and keep it across all 6 tones.

### Step 4 — Market observation (consultative follow-up)
Purpose: Position the sender as a trusted adviser.
Source: Reference a trend that comparable firms are seeing, tied back to "Company Context". Ground the observation — do not hand-wave about "the market" in general.
Content: 3-5 sentences per variant. One concrete market observation + one implication for this specific hire + a light next-step.

### Step 5 — Re-engagement (fresh angle)
Purpose: Warm re-engagement after earlier steps.
Source: Reference a DIFFERENT "Ideal Candidate Profile" from Step 2 (if two exist), or a different framing of the problem from "Core Business Problem". Signal the sender is still in the market, not nagging.
Content: Brief — 2-4 sentences per variant. One opener, one fresh angle, one low-pressure line.

# Tone definitions (apply per variant, across all 5 sequences)

- **formal** — measured, polished British business English; minimal contractions; third-person framing where natural
- **informal** — warm and conversational; first-person; contractions welcome; short sentences; friendly opener
- **consultative** — advisory and market-observation led; positions the sender as a trusted partner with a view on the wider market
- **direct** — concise and outcome-focused; cuts to the point in the first line; light on adjectives; short
- **candidate_spec** — emphasises the calibre of candidates the recruiter is talking to; references specific candidate profiles or an active pipeline
- **technical** — uses the domain language of the role (risk frameworks, quant terms, compliance regs, etc.) where appropriate, without becoming jargon-heavy

# Global rules (apply to every variant)
- Plain, ordinary British English underneath the chosen tone
- No sign-off or signature in any message
- No em-dashes, no bolding
- Never salesy; light, empathetic, gently persuasive
- Do not ask the reader for more info — this is one-way automation
- Do not name the hiring manager by their real name even if identified above; refer to them by title or generically ("your team", "the risk team")

# Output schema

Return this exact JSON shape. Replace "..." with real content. Every sequence MUST have all six tone keys populated. No tone may be null or empty-string for subject/body:

{{
  "emails": [
    {{"sequence": 1, "variants": {{
      "formal":         {{"subject": "...", "body": "..."}},
      "informal":       {{"subject": "...", "body": "..."}},
      "consultative":   {{"subject": "...", "body": "..."}},
      "direct":         {{"subject": "...", "body": "..."}},
      "candidate_spec": {{"subject": "...", "body": "..."}},
      "technical":      {{"subject": "...", "body": "..."}}
    }}}},
    {{"sequence": 2, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 3, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 4, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}},
    {{"sequence": 5, "variants": {{ "formal": {{...}}, "informal": {{...}}, "consultative": {{...}}, "direct": {{...}}, "candidate_spec": {{...}}, "technical": {{...}} }}}}
  ]
}}

Before returning, verify:
- the `emails` array has exactly 5 sequence objects (sequences 1-5)
- each sequence has all 6 tone keys populated
- each variant leans on at least one specific dossier detail (not generic prose)
- no email names the hiring manager by first name
- within a given sequence, all 6 tones carry the same underlying message, differing only in register
"""
