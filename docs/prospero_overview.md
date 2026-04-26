# Prospero — Project Overview

**A recruitment intelligence platform for executive-search agencies.**

**Audience**: Stakeholders, prospective customers, partner agencies, non-technical reviewers.
**Last updated**: 2026-04-26.
**Companion**: [docs/prospero_architecture.md](prospero_architecture.md) — the full technical version.

---

## 1. The one-paragraph version

Prospero finds every open finance / risk / quant / compliance / audit / cyber / legal job worth pursuing, generates a 1-page intelligence brief on each one (what the company really needs, who the hiring manager is likely to be, two candidate profiles to pitch), then drafts a personalised five-email outreach sequence in the agency's voice and sends it through Microsoft 365 — automatically pausing the sequence the moment the prospect replies. It's a SourceWhale-class product that combines intelligence, lead-gen, and outreach in one tool, with the intelligence layer as the moat.

---

## 2. The problem

Executive-search agencies in finance spend most of their time on three jobs that the market does badly:

| Job | What's broken today |
|---|---|
| **Finding leads** | Aggregators are noisy. LinkedIn is closed. Direct ATS scraping is fragile. Most agencies rely on a researcher with bookmarks. |
| **Researching the lead** | The role spec is rarely what the business actually needs. Working out the real problem, the right candidate profile, and who the hiring manager is takes 30-60 minutes per role. |
| **Reaching out** | Generic outreach gets ignored. Personalised outreach is a craft that can't scale past 20-30 conversations a week per recruiter. |

Existing tools (SourceWhale, the closest analogue) handle outreach but leave research as a manual job. Most agencies are still doing all three steps by hand.

## 3. What Prospero does

Prospero is a single product that does all three jobs end-to-end:

### 3.1 Discover

We scrape ~1,500 sources continuously — direct ATS systems (Workday, Greenhouse, Lever, iCIMS, Oracle Cloud, SuccessFactors and 30+ others), aggregators (Adzuna, Reed, Google Jobs, CoreSignal), and any niche job board (via a generic browser-based scraper).

Filters strip out jobs that are out of scope (wrong country, in-house recruiter roles, irrelevant titles) before they reach the database. What survives is a clean list of *real, in-scope* roles.

### 3.2 Score

Every surviving job gets a quality score made from six signals: how relevant the title is, whether the location is identifiable, how recent the posting is, how reliable the source has been historically, how complete the data is, and how confident our classifier is about the role's market. Score ≥ 0.75 → **accepted**; 0.45-0.75 → **review**; below 0.45 → **rejected**.

### 3.3 Generate intelligence

For accepted leads, an LLM produces an 8-section dossier:

1. **Company context** — what the company does, its peers, macro forces affecting it.
2. **Core problem** — the real reason this role exists, beneath the JD.
3. **Stated vs actual** — up to 4 mismatches between what the JD asks for and what the business probably needs.
4. **Spec risk** — up to 4 risks in the spec (over-spec'd salary band, unrealistic experience demands, ambiguous reporting line, etc.).
5. **Candidate profiles** — two named profile sketches the recruiter could realistically place.
6. **Lead score (1-5)** — a commercial judgement about whether this role is worth pursuing.
7. **Hiring manager search** — up to six named candidates for who the hiring manager is, ranked by confidence, sourced from real-time web search.
8. **LinkedIn boolean** — a ready-to-paste search string for the recruiter's research.

Then a second pass produces a 30-email outreach campaign — five sequences × six tones (formal, informal, consultative, direct, candidate-spec-driven, technical). The recruiter picks the tone that fits their relationship with the prospect and launches.

### 3.4 Send and track

The chosen tone's five emails go out automatically over four weeks (days 0, 7, 14, 21, 28) via the recruiter's own Microsoft 365 mailbox — they appear as if the recruiter sent them. The moment the prospect replies, all remaining emails in the sequence are cancelled and the recruiter takes over. Manual cancel is one click.

## 4. The intelligence moat

The dossier is what makes Prospero different from outreach-only tools.

- **Real research, not training data.** The dossier model uses live web search at generation time. Company context reflects what's true today — recent earnings calls, press, regulatory actions — not a training-data snapshot.
- **The "stated vs actual" section is the killer feature.** Most JDs are written by HR. The real role is shaped by the business. We surface the gap explicitly so the recruiter can pitch candidates whose CVs may not match the JD but who solve the actual problem.
- **Hiring manager named, not implied.** A separate search step finds real LinkedIn profiles of likely hiring managers, with confidence scores. The recruiter walks into the conversation knowing who they're talking to.
- **Personalised, not templated.** The campaign uses the recruiter's actual sent emails as voice training. Once a recruiter has sent a few real emails through the system, every future campaign sounds like them.

## 5. What's built and what's not

### 5.1 Built and live

- Scraping layer (35 ATS adapters + generic fallback) — production, ~120k jobs in DB.
- Pipeline (filter, classify, score) — production.
- Dossier generation — production, ~$0.135 per dossier in LLM costs.
- Campaign generation — production.
- Voice layer (per-recruiter personalisation) — code complete.
- Frontend (Sources, Leads, Dashboard, Builder, Settings) — production.
- Microsoft Graph integration for sending and reply detection — code complete and tested in dry-run.

### 5.2 Built but not yet live

- **Live email sending.** The send code is finished and exhaustively tested in a dry-run mode that exercises the full lifecycle without actually contacting Microsoft. **Keybridge security approval has been granted (2026-04-26).** To turn live-send on, we now need:
  1. **Microsoft Entra app registration** with `Mail.Send` and `Mail.ReadBasic` application permissions (one Azure config step).
  2. **Two missing API endpoints** — the "Launch Campaign" and "Cancel Campaign" buttons in the UI need their backend wires connected (~50 lines of code, half a day of work).
  3. **Smoke test** in production with the dry-run switch still on, then flip the switch.

  None of these are blocked on Prospero engineering. Once the Entra app exists, we're a half-day of work from go-live.

### 5.3 Not built

- **Multi-tenant SaaS.** Currently a single-tenant build optimised for one agency. Multi-tenancy is a 12-month roadmap item.
- **Voice tuning feedback loop.** The system imitates voice but doesn't yet adjust voice based on which emails get replies.
- **Cost dashboard in the UI.** We track per-lead LLM cost in the database; we don't yet show it to operators.
- **Streaming dossier responses.** Dossier generation takes ~15-60 seconds; today the UI waits for the full result. Streaming would give faster perceived response.

## 6. Cost and scale

### 6.1 Per-lead cost

| Step | Cost |
|---|---:|
| Dossier (LLM with web search) | ~$0.06-0.08 |
| Hiring-manager search (SerpApi + LLM extraction) | ~$0.046 |
| Campaign (LLM, 30 emails) | ~$0.025-0.03 |
| **Total per lead** | **~$0.135** |

### 6.2 Scale

| | Today | Near-term target | Long-term |
|---|---|---|---|
| **Sources** | ~1,500 | ~3,000 | unbounded |
| **Jobs in DB** | ~120,000 | ~500,000 | unbounded |
| **Recruiters** | 1 (dev) | 25-30 (BS rollout, ~June 2026) | multi-tenant SaaS, 12+ months out |
| **Architecture** | Single-replica dev | Single-tenant Container Apps | Multi-tenant SaaS |

Hosting target is **Azure Container Apps** with managed Postgres, managed Redis, and a Key Vault for secrets. Estimated infrastructure cost ~£25-50/month at the BS scale (database + Redis + Container Apps minimum + log analytics).

## 7. Why this works commercially

- **Outreach is table stakes.** SourceWhale ships outreach; we have to match it (and do).
- **Intelligence is the moat.** A dossier that surfaces the *actual* role, the *real* hiring manager, and a credible candidate pitch in 60 seconds is something a recruiter would otherwise need an analyst to produce. We sell the analyst's time back to the recruiter.
- **Lead-gen completes the picture.** SourceWhale assumes you bring your own leads. Prospero finds them, judges them, *and* drafts the outreach — no other tool ties the discovery layer to the intelligence layer.
- **Cost is asymmetric.** We charge a per-recruiter SaaS fee; LLM costs are ~$0.135 per dossier. A recruiter sending 20 campaigns/week on Prospero burns ~$11/month in LLM cost. Margins are very high.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Microsoft Graph approval is denied or delayed.** | The send code works against the same Graph API that any Outlook integration uses. Approval is a security review of the agency's Entra app, not of Prospero. Standard process. |
| **LLM costs scale faster than revenue.** | We've already done one cost-down pass (search context "high" → "medium" saved ~$0.017/lead; reasoning effort "medium" → "low" saved ~$0.020/lead). Provider toggle (DeepSeek vs OpenAI) gives a second cost lever without code changes. |
| **Source scrapers break when sites redesign.** | Generic browser fallback covers any site without a dedicated scraper. Per-source health monitoring catches breakage early. We have a "capture mode" that snapshots failing pages so selectors can be extended quickly. |
| **Email deliverability degrades with volume.** | Sending happens through each recruiter's own Microsoft 365 mailbox at conservative volume (5 emails per prospect, weekly cadence). No bulk-send infrastructure to flag spam filters. |
| **GDPR / unsubscribe compliance.** | Pre-launch checklist includes legal review of the email footer (unsubscribe link, sender identity, GDPR text). |
| **One recruiter's settings shouldn't reach another's data.** | Currently single-tenant — all data is scoped to one agency. Multi-tenant rollout will add tenant isolation at the database layer (12-month item). |

## 9. Decisions worth surfacing to non-technical readers

A few choices made during build that have downstream commercial implications:

1. **We didn't go multi-tenant yet.** It would have added 30% to the build time and locked in design decisions before we know what tenants look like. Single-tenant first, multi-tenant when the SaaS pipeline is concrete.
2. **We use OpenAI as default, with DeepSeek as a runtime toggle.** No code change is needed to flip providers. If a customer requires data residency in a specific jurisdiction, this can be done by switching providers per-tenant at config time.
3. **We send via the recruiter's mailbox, not via a Prospero relay.** Higher deliverability, no shared sender reputation, simpler compliance — at the cost of needing the customer's Entra approval.
4. **Voice training is opt-in.** Recruiters' first campaign is generic-tone. After they save 2-3 of their own previous emails as training samples, future campaigns sound like them. This is a deliberate "show value first, ask for setup second" choice.
5. **The "Save as training sample" button is the only way voice samples accumulate today.** Once live-send is on, real sent emails feed voice automatically. Until then, recruiters bootstrap by pasting their own past emails into the Builder.

## 10. Roadmap

### Q2 2026 (now)
- Live-send launch (Keybridge approved 2026-04-26; pending Entra app registration + PR D).
- BS rollout to 25-30 recruiters.
- Two location-data quality fixes (city coverage, region wiring).
- Adapter rename (`vacancysoft` → `prospero` at the package level).

### Q3-Q4 2026
- Voice tuning feedback loop (improve tone guidance from reply rates).
- Cost dashboard in UI.
- Adapter coverage expansion (Avature, njoyn, JSON-LD fallback).
- Operator-facing report exports (per-recruiter activity, per-firm pipeline).

### 2027+
- Multi-tenant SaaS migration (tenant isolation, billing, admin).
- Streaming dossier UI.
- A/B testing harness for prompts and models.
- Sub-specialism classification refinements (e.g. macro vs equity hedge fund split).

---

## 11. The 30-second elevator pitch

Prospero is what SourceWhale would look like if it knew everything about the role before you sent the first email. We scrape the jobs, judge them, write a 1-page intelligence brief on each, draft a 5-email outreach sequence in your voice, and send it through your Microsoft 365 — pausing the moment the prospect replies. It's three jobs (discovery, research, outreach) collapsed into one product, and the research layer is the moat that nobody else has built.

---

## 12. Where to go next

| If you want… | Read… |
|---|---|
| The full technical architecture | [docs/prospero_architecture.md](prospero_architecture.md) |
| The Azure deployment plan | [docs/deployment_plan.md](deployment_plan.md) |
| The outreach launch plan | [docs/launch_plan.md](launch_plan.md) |
| The known limitations register | [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) |
| The campaign-builder change history | [docs/CAMPAIGN_BUILDER_CHANGELOG.md](CAMPAIGN_BUILDER_CHANGELOG.md) |
| The dependency / runtime list | [docs/dependencies.md](dependencies.md) |

---

**End of overview.**
