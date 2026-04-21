"""Campaign email generation prompt template.

Two generations coexist here, selected at runtime by
``configs/app.toml [intelligence] campaign_template_version``:

- **v2 (default, 2026-04-20+)**: ``CAMPAIGN_TEMPLATE_V2``. "Tone
  controls content" philosophy — each of the six tones has a FIXED
  home dossier section and a fixed anchor concept that carries
  through all five sequences. The six variants within a single
  sequence are genuinely different emails (six parallel five-email
  arcs), not six voicings of the same message. Source:
  ``~/Desktop/Prospero_Prompts.xlsx`` Campaign sheet. Does not use
  ``{outreach_angle}``.

  Revision 2026-04-20b: added two global rules requiring every
  email to close with a concrete offer of value (call, shortlist
  sample, market briefing, etc.) varied across the five-sequence
  arc, so the hiring manager receives five distinct concrete
  offers over the campaign rather than five "happy to chat" nudges.
  Compatible with the existing "do not ask the reader for more
  information" rule — offering is not asking.

- **v1 (legacy, dormant)**: ``CAMPAIGN_TEMPLATE_V1``. "Same message,
  different voice" philosophy — all six tones within a sequence
  convey the same core message, just re-voiced. Uses
  ``{outreach_angle}`` from ``category_blocks.py``. Kept as a
  hot-swap rollback target; flip ``campaign_template_version = "v1"``
  in app.toml and restart the API + worker to revert without a code
  deploy.

Placeholders common to both versions:
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

Placeholder only used by v1 (silently ignored by v2's ``.format()``):
  {outreach_angle}              — domain-specific recruiter positioning
"""

CAMPAIGN_SYSTEM = "You are a specialist agency recruiter writing outreach emails. Return valid JSON only."


# ── v2 — default (2026-04-20+) ──────────────────────────────────────
# Operator-authored. Philosophy: tone determines content source, not
# just voice. Six tones → six distinct five-email arcs, each anchored
# on one concrete dossier detail that persists across sequences.
CAMPAIGN_TEMPLATE_V2 = """\
You are writing a five-step email sequence for a recruiter reaching out to the hiring manager about filling this role. For each step you will produce SIX tone variants so the operator can pick the right register per send.
# Role context

Company: {company}
Role: {title}
Location: {location}
Likely hiring manager: {hiring_manager_line}

# Intelligence Dossier

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

Return EXACTLY FIVE emails in a JSON array. Each email object has a `sequence` (1-5) and a `variants` object holding SIX tone keys: formal, informal, consultative, direct, candidate_spec, technical. Every tone must be populated for every sequence. 5 sequences x 6 tones = 30 {{subject, body}} pairs total.

IMPORTANT — read this carefully. In this prompt, TONE CONTROLS CONTENT, not just voice. The six variants within a single sequence are NOT re-voicings of the same email. They are six genuinely different emails, each drawing from a fixed dossier source dictated by the tone. What the SEQUENCE controls is the stage-appropriate framing (introduction, early-stage pain, mid-stage pain, late-stage pain, sign-off).

Within a single tone, across all five sequences, the emails form one coherent campaign built around a specific anchor (see "Campaign anchors" below). Six tones produce six distinct five-email arcs.

# Tone -> source mapping (FIXED — do not deviate)

Each tone has one home dossier section. Every variant across all 5 sequences must keep drawing from its home source. This is how the six campaigns stay distinct across the whole sequence.

- **formal** — source: Company Context. Measured institutional framing. Polished British business English, minimal contractions, third-person where natural.
- **informal** — source: Stated Need vs Actual Need. Voice: an experienced financial services recruiter writing in their own words. Approachable, gender neutral, positive. British English, specifically estuary English — common language, occasional colloquialisms, reads like it sounds spoken aloud. Avoid jargon. Don't be corny. First-person, contractions welcome, short sentences. Lean on the gap between what the JD asks for and what the business probably needs, framed as something the hiring manager is likely feeling — never as a technical diagnosis.
- **consultative** — source: Company Context + Core Business Problem (blended). Market-observation led, positions sender as a trusted partner with a view on the wider market.
- **direct** — source: Core Business Problem, stripped to one line. Concise and outcome-focused, cuts to the point in the first line, light on adjectives, short.
- **candidate_spec** — source: Ideal Candidate Profiles. Leads with a live candidate or pipeline. References a specific profile, their background, and why they fit.
- **technical** — source: Specification Risk OR Stated Need vs Actual Need. Names the domain tension using the language of the role (risk frameworks, quant terms, compliance regs) without becoming jargon-heavy.

Every variant must lean on one or more specific details from its mapped dossier section. Generic outreach that could apply to any firm is a failure. Reference the company's actual situation, the actual gap, or a specific spec risk. Do not invent details that aren't in the dossier.

# Campaign anchors (how cross-sequence consistency works)

At Sequence 1, for each tone, pick ONE concrete anchor from that tone's home dossier source. An anchor is a specific, nameable thing — not a theme. Valid anchors look like:

- **formal** anchor: one specific element of Company Context (a named strategic shift, a named regulatory pressure, a named business line)
- **informal** anchor: one human-framed question framed around stated need v actual need (expressed as something the hiring manager is likely feeling and non technical)
- **consultative** anchor: one specific market dynamic that ties Company Context to Core Business Problem
- **direct** anchor: the Core Business Problem in its sharpest form, plus one consequence of it (broad enough to evolve across five stages without repetition)
- **candidate_spec** anchor: one specific Ideal Candidate Profile presented as real and based on the archetype they represent
- **technical** anchor: one specific Specification Risk OR one specific Stated vs Actual gap

That anchor carries through all 5 sequences for that tone. Sequences 2-5 do NOT restate the anchor — they view it from the stage-appropriate angle:

- Sequence 1: introduce the anchor
- Sequence 2: the anchor seen through an early-stage hiring pain
- Sequence 3: the anchor seen through a mid-stage hiring pain
- Sequence 4: the anchor seen through a late-stage hiring pain
- Sequence 5: a final reference to the anchor, with the tone-appropriate CTA

The anchor is not the same sentence repeated. It is the same subject, examined through a different question at each stage. If Sequence 3 of a tone could plausibly sit in a different campaign about a different topic, the anchor has been lost — rewrite.

Anchors differ across tones. The candidate_spec anchor is a candidate; the technical anchor is a risk; they are not the same thing viewed differently. Six tones, six different anchors, six coherent five-email arcs.

# The five sequences (stage-appropriate framing)

Each sequence defines the job-to-be-done at that stage. The tone determines WHAT the email is about (via the mapping above) and WHICH anchor carries through the campaign; the sequence determines WHERE in the process the hiring manager is and what pain the email speaks to.

### Sequence 1 — Initial outreach (week 1)
Every variant, regardless of tone, must read like an introductory email. Open with a greeting, a brief introduction of who the sender is (Barclay Simpson, the market they cover), then pivot into the anchor chosen for that tone. Close with a low-pressure next step. Keep it light. Do not assume prior contact.

### Sequence 2 — Early-stage pain (week 2)
By now the role has been live for a couple of weeks. The hiring manager may be seeing thin inbound, a shortlist that feels off, or early signs the spec isn't landing with the market. View the tone's anchor through one plausible early-stage pain — either from the dossier's Specification Risk / Stated vs Actual sections, or a generic early-stage hiring pain (thin shortlist, wrong-calibre CVs, internal pressure to move faster) — whichever fits the tone's home source best. Speak to the pain through the anchor, don't just list the pain. 3-5 sentences.

### Sequence 3 — Mid-stage pain (week 3)
Mid-process pains: candidates dropping out, counter-offers landing, scope creep, second-round fatigue, realisations that the original spec needs adjusting. View the tone's anchor through one such pain — dossier-sourced or generic — that aligns with the tone's home source. Speak to it with a light view on how the anchor (the candidate archetype, the market dynamic, the spec risk, etc.) handles or illuminates that mid-stage moment. 3-5 sentences.

### Sequence 4 — Late-stage pain (week 4)
Late-stage pains: process fatigue, pressure from above, rethinking whether the spec itself is the problem, the role going quiet internally, budget questions. View the tone's anchor through one such pain — dossier-sourced or generic — that aligns with the tone's home source. Keep it observational and calm, not alarmist. 3-5 sentences.

### Sequence 5 — Sign-off with CTA (week 5+)
A clean warm sign-off. Brief — 2-4 sentences. The email makes one final reference to the anchor, then delivers the tone-appropriate CTA:

- **formal** — offer a measured market update conversation grounded in Company Context, framed around the anchor.
- **informal** — leave the door open in a human way, with a light "if it's still live, I'm here" ask that references the anchor.
- **consultative** — offer a market briefing or a short call to share what comparable firms are doing on the anchor dynamic.
- **direct** — one crisp line asking if the role is still open and whether it's worth a short call, referencing the anchor problem.
- **candidate_spec** — offer to share the anchor candidate (or someone matching the archetype) if the role is still live.
- **technical** — offer a pointed conversation about the anchor spec tension or risk, framed as a calibration check.

Every Sequence 5 variant must feel like a genuine sign-off, not another nudge. Signal the sender is still in the market, not nagging.

# Global rules (apply to every variant)

- Always start with a greeting.
- Plain, ordinary British English underneath the chosen tone.
- Always close with "Kind regards" or similar. No further sign-off block or signature.
- No em-dashes. No bolding. Do not use "shifted" in place of "change".
- Never salesy. Light, empathetic, gently persuasive.
- Do not ask the reader for more information — this is one-way automation.
- Every email must close with one concrete offer of value the sender is willing to give — e.g. a call, a shortlisted CV, a short market briefing, a salary benchmark, a trend observation, a competitor-hiring datapoint, an intro to someone adjacent, a pen portrait of a candidate pattern. Never end with a vague "let me know if interested" or "happy to chat". The offer is what the sender is prepared to give, not a request for the reader to act on. This is compatible with the previous rule — offering is not asking.
- Vary the offer across the five sequences within each tone-arc. No two sequential emails in the same arc should close with the same offer. The Sequence 5 CTA is already tone-specific; the offers used in Sequences 1-4 must be different from each other and from that CTA, so the hiring manager receives five distinct concrete offers over the campaign.
- Do not name the hiring manager by their real name even if identified above. Refer to them by title or generically ("your team", "the risk team").
- Do not invent dossier details. If a dossier section is thin, lean on what IS there rather than fabricating.

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
- each variant draws from its mapped dossier source (formal->Company Context, informal->Core Business Problem, consultative->Company Context + Core Business Problem, direct->Core Business Problem, candidate_spec->Ideal Candidate Profiles, technical->Specification Risk / Stated vs Actual)
- within each tone, the 5 sequences share a single concrete anchor (named candidate, named business problem, named spec risk, etc.) — not a vague theme
- within each tone, no sequence could plausibly sit inside a different tone's campaign
- sequence 1 feels introductory for all 6 tones
- sequence 5 is a genuine sign-off with a tone-appropriate CTA referencing the anchor
- every email closes with one concrete offer of value (not a vague "happy to chat"); the five offers within each tone-arc are distinct from each other
- no email names the hiring manager by first name
- no em-dashes, no bolding, no "shifted" in place of "change"
- every email ends with "Kind regards" or similar
"""


# ── v1 — legacy (rollback target) ───────────────────────────────────
# Frozen as at 2026-04-20. Do NOT edit; iterate on v2 instead. Retained
# so ``campaign_template_version = "v1"`` reverts behaviour byte-for-byte.
CAMPAIGN_TEMPLATE_V1 = """\
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


# Back-compat alias. Anything importing ``CAMPAIGN_TEMPLATE`` directly
# gets the legacy v1 template. The resolver now selects v1 / v2
# explicitly via the config flag, so this alias exists only for
# out-of-tree importers and deferred deletion.
CAMPAIGN_TEMPLATE = CAMPAIGN_TEMPLATE_V1
