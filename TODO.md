# Deferred work

## Ticket — Dedupe `_extract_employer_from_payload`

**Goal**: get rid of the duplicate aggregator-employer extractor so adding
a new aggregator (or fixing one) only needs a single edit.

### What's wrong

There are now two implementations of `_extract_employer_from_payload`
(was three until commit XXXX inlined the third site to call the API
copy):

| Location | Coverage |
|---|---|
| [`src/vacancysoft/pipelines/enrichment_persistence.py:40`](src/vacancysoft/pipelines/enrichment_persistence.py:40) | **Comprehensive** — knows Adzuna `company.display_name`, Reed `employerName`, Google Jobs `company_name`, eFinancialCareers `companyName` / `advertiserName` / `employer.name`, and a generic string-`company` fallback |
| [`src/vacancysoft/api/server.py:522`](src/vacancysoft/api/server.py:522) | **Leaner** — only knows `company.display_name`, `employer_name`, `employerName`, `companyName`, `company_name`. Used by both `/api/sources` card aggregation AND `/api/dashboard` Live Feed lead rendering. |

These have drifted three times now. First the Sources page chip was
missing Reed (commit 6401fbb fixed by adding `employerName` to the
API copy). Then the Live Feed showed "Reed" instead of the real
employer because the dashboard endpoint had a third **inline** copy
of the same logic — now collapsed into the API copy. The pattern
is: someone adds a new aggregator key in one place and the others
silently break. Without dedupe to a single shared module, this
recurs every time a new aggregator lands.

### The fix

1. Make the enrichment version the single source of truth — it's
   already the more thorough one and is import-safe (no DB session
   needed for the function itself, just imports `dict | None`).
2. Move it to a neutral location, e.g.
   `src/vacancysoft/intelligence/payload_extract.py` or
   `src/vacancysoft/utils/aggregator_payloads.py`, so neither
   `enrichment_persistence` nor `api/server` "owns" it.
3. Replace both call sites with imports from the new location.
4. Delete the duplicate.
5. Verify: `grep -rn '_extract_employer_from_payload' src/` should
   show definitions only in the new module, with imports everywhere
   else.

### Estimated effort

~15 minutes plus a smoke test that one Reed source card and one
Adzuna source card still surface employers correctly via
`/api/sources`. No schema or migration work.

### Out of scope

- Adding new aggregators (do that as separate work, but they only
  edit the new shared module).
- Refactoring the broader API/enrichment split (this is a focused
  dedup, not an architectural reshuffle).

---

## Ticket — Per-user campaign-voice learning (few-shot, not fine-tuning)

**Goal**: when a user edits a generated campaign email, store the edit and use
their accumulated edits as few-shot examples in future campaign generations
so the model writes in their personal voice over time.

### Why few-shot, not fine-tuning

Earlier discussion analysed both approaches:

| | Few-shot (this ticket) | Fine-tuning |
|---|---:|---:|
| Per-campaign cost premium | ~+8% (~$0.0006) | ~+100% (~$0.008) |
| Setup per user | $0 | ~$0.25 / retrain |
| Time for new edits to take effect | Immediate (next call) | Hours (after retrain completes) |
| Works with reasoning models (gpt-5*) | Yes | Often not — fine-tuning isn't universal in the gpt-5 family |
| Operator complexity | Low | High (model lifecycle per user) |
| Annual uplift at 10 users / 1k campaigns/mo | ~£5-10/yr | ~£90-100/yr |

Fine-tuning would be overkill — few-shot in-context learning produces the
same style adaptation at <10% of the cost and zero training infrastructure.

### Schema

New table `campaign_edits` (migration 0007 or whatever's next):

| column | type | notes |
|---|---|---|
| id | UUID PK | |
| user_id | FK → users.id | requires the multi-user system to exist first |
| campaign_output_id | FK → campaign_outputs.id | the campaign whose email was edited |
| sequence | int | which email in the 5-step sequence (1-5) |
| tone | string | which tone variant (formal, informal, etc.) |
| original_subject | text | what the model produced |
| original_body | text | what the model produced |
| edited_subject | text | what the user actually sent |
| edited_body | text | what the user actually sent |
| edit_distance | float nullable | optional — Levenshtein or similar to score how much was changed |
| created_at | datetime | |

Index on `(user_id, created_at desc)` so retrieval of recent edits is fast.

### Code touch points

- **[`src/vacancysoft/intelligence/campaign.py`](src/vacancysoft/intelligence/campaign.py)** —
  before calling `call_chat`, query the user's most recent N edits (likely
  N=3-5), format as in-context examples, prepend to the prompt. Pass
  `user_id` through the call signature.
- **[`src/vacancysoft/intelligence/prompts/base_campaign.py`](src/vacancysoft/intelligence/prompts/base_campaign.py)** —
  add a "User voice examples" section to the system or developer prompt
  that the few-shot examples slot into.
- **API** — new `POST /api/leads/{id}/campaign/edit` endpoint that accepts
  the edited email and stores a `campaign_edits` row. Tied to the
  authenticated user.
- **Frontend** ([`web/src/app/builder/page.tsx`](web/src/app/builder/page.tsx)) —
  the campaign builder already has email editing. Add a "save changes"
  button that hits the new endpoint. Currently edits are state-only per
  the `CAMPAIGN_BUILDER_CHANGELOG.md` note.

### Open questions to resolve before implementation

1. **Is the dossier shared or per-user?** If users share the same lead pool
   but each gets their own personalised campaign, that multiplies campaign
   cost by N (one campaign call per user per lead). Cheapest model: dossier
   generated once and shared, only campaigns are per-user. Most expensive:
   dossier and campaign both per-user (full personalisation). Decide at
   multi-user-system design time.
2. **How many examples?** Start with N=3 most recent. Validate on a real
   user before bumping to 5. Above 5 gets pricey.
3. **Selection strategy.** Most recent vs most representative vs most
   different from base output? "Most recent" is simplest; "most different"
   gives the model the strongest style signal but takes effort to compute.
4. **Cold start.** A new user with zero edits gets the base voice. Worth
   surfacing in UI so they understand the model gets better with usage.

### Estimated impact

- **Cost**: campaign cost rises from ~$0.0086 to ~$0.0092 per call (~7%
  uplift). Storage is trivial. No new model deployments or training
  infrastructure.
- **Quality**: dependent on how distinctive each user's voice is. For
  recruiters with strong personal style, expect noticeable improvement
  after ~10-20 edits. For users whose edits are mostly typo fixes, less
  benefit — and that's fine, the model defaults to base voice.

### Acceptance criteria

- [ ] `campaign_edits` table created via migration with proper FKs.
- [ ] `POST /api/leads/{id}/campaign/edit` endpoint persists edits, scoped
      to the authenticated user.
- [ ] `generate_campaign` accepts a `user_id` arg, retrieves top N edits
      (default 3), and includes them as in-context examples in the prompt.
- [ ] Frontend builder has a working "save my changes" action that hits
      the new endpoint.
- [ ] Cost telemetry continues to work — `cost_usd` on `campaign_outputs`
      reflects the slightly bigger prompts.
- [ ] Smoke test: edit one email, regenerate the campaign for the same
      lead, and observe that the new generation is closer in style to the
      edit than to the original.

### Out of scope

- Fine-tuning. We deliberately picked few-shot for cost and flexibility.
- Cross-user voice transfer (e.g. "write like Bob"). Personal voice is
  per-user only.
- Detecting and ignoring "bad" edits (e.g. user accidentally pasted
  unrelated text). Could be a follow-up if it becomes a problem.

### Pre-requisites

This ticket can't start until the multi-user system exists (`users` table,
authentication, session/identity threaded through API requests). Reference
the existing memory note in `~/.claude/projects/.../MEMORY.md`:
"multi-user planned for later".
