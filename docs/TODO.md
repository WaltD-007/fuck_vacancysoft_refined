# Deferred work

> **For active sequencing and forward planning, see [launch_plan.md](launch_plan.md).**
> That document supersedes this file as the "what to do next" tracker. This
> file remains the decision log — deeper per-ticket rationale that the
> launch plan only summarises.

## Ticket — Surface zero-classification direct sources on the Sources page

**Symptom**: No Jobs Found tab on `/sources` is permanently empty.

**Why**: `_build_source_card_ledger` in `src/vacancysoft/api/server.py:540`
builds employer cards from the lead pool — only sources that have at
least one classified lead in a core market produce a card. A direct
source whose adapter ran and returned zero raw_jobs (or returned jobs
that none classified into core markets, with no aggregator picking up
the slack) never gets a card, so the frontend has nothing to render in
the No Jobs Found view.

The frontend's filter is correct:
`!isBroken && jobs === 0 && adapter !== "aggregator" && scored === 0`.
The data just doesn't exist client-side.

**The fix**: extend `_build_source_card_ledger` to also walk the
`direct_sources` list and emit a synthetic card for each direct source
that did NOT match any existing card by `employer_norm`. Such a
synthetic card would have:
- `card_id` = the Source.id (positive)
- `adapter_name` = the actual adapter (e.g. "workday")
- `jobs` = `raw_counts.get(src.id, 0)` (likely 0 here, but accurate)
- `scored` = 0 (no classifications)
- `categories` / `sub_specialisms` / `aggregator_hits` = `{}`
- `last_run_status` / `last_run_error` from the latest SourceRun

That way the No Jobs Found tab populates with all currently-empty
direct sources and the operator can re-scrape them via the existing
Update button workflow.

**Risk**: the cards endpoint already returns ~5,000 rows. Adding
zero-classification sources could push that into the tens of
thousands and make the API slower. Mitigation: paginate the API
response, or surface this set behind a separate endpoint
(`/api/sources/empty`) that the No Jobs Found tab calls only when
selected.

**Estimated effort**: 30-60 minutes plus an API smoke test. No
schema or migration work.

---

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

---

## Follow-ups flagged during the 2026-04-19 refactor session

Priority scores use a 1–5 scale:
**P1** = urgent / blocking · **P2** = high · **P3** = medium · **P4** = low · **P5** = trivial / cosmetic.

| # | Ticket | Priority | Effort | Status |
|---|---|---|---|---|
| 1 | Add minimal CI (GitHub Actions) | **P2** | 1 h | ✅ `34d0561` |
| 2 | Shared `_canonical_employer_norm()` helper | **P3** | 30 min | open |
| 3 | Worker-side cache invalidation after `scrape_source` | **P3** | 20 min | open |
| 4 | Migrate `@app.on_event` startup/shutdown to `lifespan` | **P3** | 1 h | open |
| 5 | Kill legacy `models_v2.py` naming; decide on `db/repositories/` | **P3** | 1 h | ✅ `f1b2456` |
| 6 | Fix or xfail `test_classification.py::test_relevant[Pricing Actuary]` | **P4** | 15 min | ✅ `163d8e9` (xfail) |
| 7 | Promote sources-page derived helpers into `web/src/app/sources/utils.ts` | **P4** | 20 min | open |
| 8 | Add `response_model=` to the 9 dict-returning handlers | **P4** | 45 min | open |
| 9 | Per-company aggregator probe for stronger "No Jobs Found" signal | **P4** | 2–3 h | open |
| 10 | Fold the filter-label / "Clear filter" block into `StatsSection` | **P5** | 10 min | open |
| 11 | Delete `_addcompany_count_jobs` if confirmed unused | **P5** | 5 min | open |
| 12 | Composite index on `SourceRun(source_id, created_at)` if hotspot | **P5** | 15 min (conditional) | open |
| 13 | Investigate DeepSeek for dossier + campaign properly | **P5** | 4–6 h | open (deprioritised — two runs confirmed DeepSeek can't match OpenAI here) |
| 14 | Investigate why HM search returned zero hiring_managers | **P3** | 1–2 h | open |
| 15 | Fix `'NoneType' object has no attribute 'lower'` crash in intelligence client | **P3** | 30 min | open |

---

### Ticket 1 — Add minimal CI (GitHub Actions) [P2]

**Goal**: prevent silent regressions from landing on `chatgpt/adapter-updates` or `main` — one broken commit is currently all it takes.

**What**: add `.github/workflows/ci.yml` that on push / PR runs, in order:
- `pip install -e ".[dev]"`
- `pytest`
- `ruff check src tests`
- `cd web && npm ci && npx tsc --noEmit`

No deploy step, no Playwright install (keeps it under 60 s). Matrix can stay Python 3.12 only for now. Don't block on the pre-existing `Pricing Actuary` failure — see Ticket 6.

**Why P2**: everything else in this list is cleanup. CI is the one that catches the problems that would otherwise need cleanup tickets next quarter.

---

### Ticket 2 — Shared `_canonical_employer_norm()` helper [P3]

**Goal**: unify employer-name normalisation across the three sites that currently each do their own `.lower().strip()`, so employers with suffix mismatches (e.g. "Acme Inc." vs "Acme Inc") stop silently mis-bucketing on the Sources page.

**Call sites today**:
- [src/vacancysoft/api/ledger.py:530](src/vacancysoft/api/ledger.py:530) (aggregator side in `_build_source_card_ledger`)
- [src/vacancysoft/api/ledger.py:605](src/vacancysoft/api/ledger.py:605) (`direct_by_emp` key)
- [src/vacancysoft/api/ledger.py:726](src/vacancysoft/api/ledger.py:726) (aggregator `agg_matched_norms` in the injection pass)

**Minimum viable**: new `_canonical_employer_norm(name: str) -> str` in `api/ledger.py` (or a sibling `utils.py`). v1 still does just `.lower().strip()` — the extraction is the win. v2 can add trailing `Inc./Ltd./Plc/GmbH/…` stripping, ampersand↔"and" folding, NBSP collapsing, etc. — applied everywhere atomically.

**Why P3**: a real but low-volume bug; the three-site drift is the thing that makes it insidious.

---

### Ticket 3 — Worker-side cache invalidation after `scrape_source` [P3]

**Update 2026-04-19**: partially addressed by the queue self-heal landed in
commit `<this commit>`. The self-heal doesn't clear the ledger cache, but
it does solve the related "ReviewQueueItem stuck in pending with no ARQ
job" failure mode that was the more visible symptom. See
`src/vacancysoft/worker/self_heal.py`. Original ticket body below still
applies for the cache-specific work.

---

**Goal**: when the ARQ worker (`src/vacancysoft/worker/tasks.py`) finishes a scrape, the next `/api/sources` request should rebuild the ledger, not wait up to 30 s for the `_SOURCES_CACHE_TTL` to expire.

**What**: after each successful `scrape_source` completion in `worker/tasks.py`, call `clear_ledger_caches()` (already public in [src/vacancysoft/api/ledger.py:47](src/vacancysoft/api/ledger.py:47)). Caveat: the worker is a separate process — if the caches are in-process dicts, the worker calling `clear_ledger_caches()` clears *its* empty dicts, not the API server's. Real fix requires a shared signal: either (a) move caches to Redis, (b) have the worker send an HTTP POST to `/api/internal/invalidate-cache`, or (c) accept the 30 s TTL as good enough.

**Why P3**: operational nicety; 30 s staleness is tolerable but users notice the "I just scraped — where is it?" gap. Stop short of option (a) unless Redis becomes mandatory anyway.

---

### Ticket 4 — Migrate `@app.on_event` → `lifespan` [P3]

**Goal**: remove the FastAPI deprecation warning that `_startup` / `_shutdown` emit on every request and every pytest run (counted 55 during `pytest`).

**What**: in [src/vacancysoft/api/server.py](src/vacancysoft/api/server.py), replace the two `@app.on_event` blocks with an `@asynccontextmanager` `lifespan(app)` function passed to `FastAPI(lifespan=lifespan)`. The startup side already does real DB work (re-enqueueing orphaned leads) — the migration is not purely mechanical, so write a short pytest that exercises the lifespan context before landing.

**Why P3**: deprecation-only for now; `on_event` still works. Landing before FastAPI removes it is cheap insurance.

---

### Ticket 5 — Kill legacy `models_v2.py` naming + decide on `db/repositories/` [P3]

**Goal**: drop the "v2" suffix that implies a "v1" that no longer exists; decide whether the 5 near-empty repository stubs stay or go.

**What**:
1. Rename [src/vacancysoft/db/models_v2.py](src/vacancysoft/db/models_v2.py) → `models.py` (currently a 31-line re-export shim). Every import `from vacancysoft.db.models import …` already uses the shim, so the rename is a `git mv` + delete-the-shim + sed across imports.
2. [src/vacancysoft/db/repositories/](src/vacancysoft/db/repositories/) has 5 files totalling <60 lines, all near-empty stubs. Either flesh them out (become the canonical CRUD entry points) or delete and inline. Dead weight as-is.

**Why P3**: new-engineer onboarding hazard. Not urgent; becomes urgent the first time someone actually tries to find "v1" and wastes an hour.

---

### Ticket 6 — Fix or xfail `test_classification.py::test_relevant[Pricing Actuary]` [P4]

**Symptom**: pytest has shown 1 failing / 359 passing for every run this session. The test expects "Pricing Actuary" to be rated relevant, but the blocklist comment at [tests/test_classification.py:45-46](tests/test_classification.py:45) explicitly notes actuarial roles are blocklisted. So the test disagrees with the codebase, or the codebase disagrees with the test — one needs to change.

**What**: either
- flip the test to assert the title is *not* relevant (if the blocklist is the correct intent), or
- remove "Pricing Actuary" from the blocklist (if the test is correct and the blocklist is too aggressive), or
- mark it `@pytest.mark.xfail` with a reason pointing at whichever decision.

**Why P4**: pre-existing and unrelated to everything we touched this session, but a red test on every CI run (once Ticket 1 lands) is noise that teaches people to ignore the failure column.

---

### Ticket 7 — Promote sources-page derived helpers into `web/src/app/sources/utils.ts` [P4]

**Goal**: move the five closures that still live inline in `SourcesPage()` into a sibling module so both `SourceCard` and `StatsSection` can import them directly instead of receiving them as function props.

**Helpers to move**: `isBroken(s)`, `getCats(s)`, `getScored(s)`, `effCatCount(s, cat)`, `effScored(s)`. Call sites today (post-Week-3):
- defined in [web/src/app/sources/page.tsx:326-358](web/src/app/sources/page.tsx:326)
- passed as props into `SourceCard` ([page.tsx:677-679](web/src/app/sources/page.tsx:677))
- passed as props into `StatsSection` ([page.tsx:619-621](web/src/app/sources/page.tsx:619))

**Shape**: the helpers close over `countryFilter`, `filters`, and `subFilters`. A clean extraction takes those as explicit parameters — e.g. `getCats(source, countryFilter)`, `effCatCount(source, cat, subFilters)`. Drop the prop-passing in both components.

**Why P4**: cleanup, zero functional change. Mostly removes noise from the `<SourceCard ... />` prop list.

---

### Ticket 8 — Add `response_model=` to dict-returning handlers [P4]

**Goal**: every endpoint in the OpenAPI schema should have a declared response shape so the generated docs are useful.

**Handlers lacking `response_model`** (9 total):
- [routes/leads.py](src/vacancysoft/api/routes/leads.py): `get_dashboard`, `queue_campaign`, `list_queue`, `send_to_campaign`, `remove_from_queue`
- [routes/sources.py](src/vacancysoft/api/routes/sources.py): `scrape_source_endpoint`, `diagnose_source`, `delete_source`
- [routes/campaigns.py](src/vacancysoft/api/routes/campaigns.py): `generate_lead_dossier`, `get_lead_dossier`, `generate_lead_campaign` (correction: 10 not 9 — re-count before implementing)

**What**: define Pydantic models for each return shape in [api/schemas.py](src/vacancysoft/api/schemas.py), wire them in with `@router.post("/...", response_model=…)`. Purely additive — no wire-format change.

**Why P4**: internal API; schema coverage improves discoverability and catches shape regressions.

---

### Ticket 9 — Per-company aggregator probe for stronger "No Jobs Found" signal [P4]

**Goal**: upgrade the "genuine empty" signal in the card ledger ([api/ledger.py:663-774](src/vacancysoft/api/ledger.py:663)) from inferred ("aggregator ran recently and didn't mention this employer") to observed ("we asked Adzuna specifically for this employer's jobs in the last 24 h and got zero").

**What**: add an adapter method like `AdzunaAdapter.count_by_employer(name, since)` that hits Adzuna's `?what_and=&company=` (or equivalent per-aggregator) endpoint. Call it on-demand from the injection pass when cross-checking. Cache per (employer, adapter, window) for the TTL.

**Why P4**: improves the fidelity of a bucket that's already working acceptably. Not critical, but fixes the "Acme Inc." suffix-mismatch false-positive cleanly (the probe bypasses the normalisation issue entirely). Conditional on aggregator APIs supporting per-company queries — Adzuna and Reed do; Coresignal's credit cost would need weighing.

---

### Ticket 10 — Fold the filter-label / "Clear filter" block into `StatsSection` [P5]

**Goal**: finish what Week 3 step 5 didn't. The filter-label + "Clear filter" button sits inline in [web/src/app/sources/page.tsx](web/src/app/sources/page.tsx) around the current line 760, reading `filters` and `subFilters` that `StatsSection` already owns.

**What**: move the block into `StatsSection.tsx`, delete the inline copy. Passes through the existing `setFilters` / `setSubFilters` props already wired.

**Why P5**: ~10 lines of cosmetic cleanup.

---

### Ticket 11 — Delete `_addcompany_count_jobs` if unused [P5]

**Goal**: remove dead code noted during Week 4 step 5.

**What**: confirm [routes/add_company.py::_addcompany_count_jobs](src/vacancysoft/api/routes/add_company.py) has no remaining call sites (`grep -rn _addcompany_count_jobs src/ tests/`). If truly unused, delete the function and its imports (`load_taxonomy_title_phrases`, `SEARCH_ENDPOINT`).

**Why P5**: trivial.

---

### Ticket 12 — Composite index on `SourceRun(source_id, created_at)` if hot [P5]

**Goal**: speed up the "latest SourceRun per source" grouped query in the ledger's injection pass and the existing step-4 metadata loop — IF profiling shows it matters.

**What**: add an Alembic migration creating `ix_source_runs_source_id_created_at ON source_runs(source_id, created_at DESC)`. Leave `SourceRun.started_at`'s existing index intact. Don't land this speculatively — run `EXPLAIN` on the queries first and confirm a real win.

**Why P5**: conditional / speculative. The ledger is already cached for 30 s so rebuild latency is hit at most twice a minute. Only relevant if an operator complains about first-request latency.

---

### Ticket 13 — Investigate DeepSeek for dossier + campaign properly [P4 → P5, deprioritised]

**Update 2026-04-19 (post second test run):** the step-1 hypothesis from this
ticket (swap campaign primary from `deepseek-reasoner` to `deepseek-chat`,
reduce campaign scope to 1 sequence × 2 tones) was tested. Output quality
was **worse, not better**. User flipped back to OpenAI immediately and
restored the full-scope prompt (commits after `ea5bd8b`).

Two runs, two different configs, same verdict: DeepSeek does not match
OpenAI quality on this pipeline. Likely NOT a config or prompt issue —
more likely DeepSeek's training data + lack of web search produce
categorically less useful content for UK recruitment-finance outreach,
regardless of which model or how tight the budget is.

Deprioritising from P4 to P5. The provider abstraction is still in
place and dormant (zero cost); re-engage only if a compelling reason
lands (e.g. DeepSeek ships web-search support, or costs become
load-bearing). Steps 3 (parallelise campaign into 5 sub-calls) and 4
(rewrite prompts for DeepSeek-reasoner's style) from below are still
theoretically worth trying, but evidence from two runs suggests they
won't close the gap.

---

### Ticket 13 — (original body, preserved) Investigate DeepSeek for dossier + campaign properly

**Context**: during the 2026-04-19 session we wired DeepSeek in behind the `use_deepseek_for_*` toggles in `configs/app.toml`, ran ~10 real leads through `deepseek-reasoner`, and reverted. Outputs were noticeably weaker than the OpenAI path. This ticket captures what we saw so a second attempt starts from evidence rather than starting over.

Everything the earlier session landed stays in the tree — `providers.py`, the pricing entries, the config toggles, the cost-report CLI. The toggles just default to `false`. Re-enabling is still a two-line config flip.

**What the test run showed** (sample: HSBC / Head of Wholesale Credit Risk Management, Taguig)

1. **`company_context` goes generic.** Output read like a Wikipedia intro ("HSBC Holdings plc is a global banking and financial services institution headquartered in London…"). No current events, no recent HSBC announcements. Expected: DeepSeek has no web-search tool, so `web_search=True` is silently dropped in `providers.py:call_llm`. The reasoner answers from training data only.
2. **`core_problem` and `spec_risk` are actually OK.** The reasoner pulled real domain knowledge (Taguig, BSP, Basel III/IV). The analytical sections work; the research-hungry sections don't.
3. **Campaign emails are generic.** Sequence 1 formal opener is boilerplate ("I am writing to introduce my services as a specialist risk recruitment consultant…"); sequence 2 invents a candidate profile that could fit any senior risk hire anywhere. Not obviously *wrong*, just unremarkable — would be deleted by a senior HM.
4. **Per-lead latency ~300-360 s.** Dossier main ~105 s, HM ~47 s, campaign ~150 s. This hit the ARQ `job_timeout = 300 s` ceiling and killed campaigns mid-generation for every run until we bumped it to 900 s (commit `1423606`). Leave that bump in place.

**Three likely causes, none of them "DeepSeek is just bad"**

A. **Prompt was tuned for GPT-5 / reasoning-effort knob.** DeepSeek-reasoner ignores `temperature` (fixed 1.0 internally) and has no `reasoning_effort` equivalent. `providers.py` silently drops both parameters. Current prompt in [`prompts/base_campaign.py`](../src/vacancysoft/intelligence/prompts/base_campaign.py) and [`prompts/base_dossier.py`](../src/vacancysoft/intelligence/prompts/base_dossier.py) was never calibrated to this different inference profile.

B. **Token-budget strangulation on campaign.** The campaign prompt asks for 30 emails (5 sequences × 6 tones) in a single JSON object. Reasoner models emit chain-of-thought to `reasoning_content` within the same `max_tokens=16000` budget. Rough estimate: reasoning eats 8-10k tokens → ~200-270 tokens left *per email*, which forces the model toward safe generic copy.

C. **Wrong model for the job.** We defaulted the campaign primary to `deepseek-reasoner` because the user asked for the "highest-performance reasoning model". For 30 British-English outreach emails that's actively the wrong optimisation: creative-writing tasks don't benefit from hidden reasoning chains and the chain steals the budget. `deepseek-chat` (V3) is already wired as the fallback and is probably a better primary for campaigns.

**Four things to try, in order of expected ROI**

1. **Swap campaign primary from reasoner to chat.** One-line in `configs/app.toml`:
   ```toml
   campaign_model_deepseek = "deepseek-chat"
   ```
   Faster (~30 s vs 150 s), cheaper, and the reasoning-chain token-burn issue goes away. Re-queue 3 leads and compare sequence-1 formal quality against the OpenAI baseline. No code change needed. 15 min including restart.
2. **Keep dossier on OpenAI.** `use_deepseek_for_dossier = false`. The cost saving from moving dossier's main call to DeepSeek (~80% off the main-call input cost) is real, but the `company_context` degradation from losing web search is visible and load-bearing — that section is what operators read first. 0 min; just don't flip it on.
3. **Parallelise campaign into 5 per-sequence calls.** Each call produces 6 tones for a single sequence in its own JSON blob, giving each email ~3k tokens of budget instead of 200. Roughly same total tokens; much better quality per email; wall time similar because the calls run in parallel. Requires a focused edit in [`campaign.py`](../src/vacancysoft/intelligence/campaign.py): split `messages` by sequence, `asyncio.gather` the calls, merge the results. 2-3 h; worth doing even if we stay on OpenAI because it makes the fallback-on-empty-reasoner issue less likely on either provider.
4. **Rewrite the dossier + campaign prompts with DeepSeek-reasoner's strengths in mind.** Reasoner models benefit from explicit "think step by step about X, then produce Y" scaffolding; they dislike loose open-ended creative prompts. Only worth this if steps 1-3 haven't closed the quality gap. 4-6 h if done properly with side-by-side evals.

**What to keep vs revert if this ticket gets de-prioritised permanently**

- **Keep** `providers.py`, the pricing entries, the cost-report CLI, the `job_timeout = 900` bump. All are general infrastructure that cost nothing to leave in place and are useful for any future provider work (e.g. Anthropic, local models).
- **Keep** the config toggles — defaulting to `false` they're zero-cost.
- **Consider reverting** `providers.py` and the toggle plumbing only if the "always OpenAI" commitment becomes permanent AND the code clutter starts hurting. Probably not worth the effort; leave as dormant infrastructure.

**Smoke-test recipe for a second attempt**

1. Flip `use_deepseek_for_campaign = true`, leave dossier on OpenAI.
2. Change `campaign_model_deepseek = "deepseek-chat"` (step 1 above).
3. Restart worker. Queue 3 leads (pick roles you have recent OpenAI baselines for).
4. Run `vacancysoft intel cost-report --since 2h` and eyeball: cost delta, email quality, whether fallback ever fires.
5. If the campaign reads at parity or better vs the OpenAI baseline, move on to step 3 of the plan (parallelise). If it doesn't, stop.

---

### Ticket 14 — Investigate why HM search returned zero hiring_managers [P3]

**Update 2026-04-19 (evening)**: a second path for the HM call has
landed — SerpApi + `gpt-4o-mini` extraction, gated by
`use_serpapi_hm_search` in `configs/app.toml`. See
`src/vacancysoft/intelligence/hm_search_serpapi.py` and the "SerpApi
HM path" section of `docs/intelligence-providers.md`. Cost drops ~60%
per lead; quality parity vs. the OpenAI gpt-5.2 web_search path is
unverified until we run a batch.

When the real cause of the zero-HM sweeps is diagnosed (this ticket),
apply the fix to BOTH paths if it's prompt-side (shared
`_KNOWN_FAKE_NAMES` / `"former"` filter in `dossier.py`), or just the
affected path if it's search-side.

---

### Ticket 14 — (original body) Investigate why HM search returned zero hiring_managers

**Context**: surfaced during the DeepSeek A/B test in the same 2026-04-19 session. This is not a DeepSeek issue — HM search is hard-wired to OpenAI in [`dossier.py`](../src/vacancysoft/intelligence/dossier.py) — but it became visible while we were scrutinising those runs.

**Symptom**: every one of the ~10 leads tested during the session returned `IntelligenceDossier.hiring_managers = []`. The HM call completed successfully each time — `gpt-5.2-2025-12-11`, ~30k prompt tokens of web_search context, ~2k completion, $0.058 per call, 47-56 s latency. It's spending the money; it's just not returning names.

**Diagnostic steps before any code change**

1. Query historical data: `SELECT COUNT(*) FROM intelligence_dossiers WHERE hiring_managers != '[]'::jsonb AND created_at >= NOW() - INTERVAL '30 days'`. If the answer is "also zero", HM has been broken for a while and this session is the first time someone noticed. If it's "some", something regressed recently — `git log --oneline -- src/vacancysoft/intelligence/prompts/category_blocks.py src/vacancysoft/intelligence/dossier.py` to find what changed.
2. Check whether the specific leads queued during the test had genuinely findable HMs. HSBC Taguig is a Philippines role where LinkedIn coverage is patchier; "Head of Wholesale Credit Risk Management" may be an internal-transfer role that no recruiter would expect a public HM for. Pick a lead you *know* has an obvious HM (named partner at a boutique, head-of-trading at a mid-sized prop shop) and see whether HM search finds them.
3. Temporarily bump logging in [`dossier.py::_build_hm_prompt`](../src/vacancysoft/intelligence/dossier.py) to log the raw parsed response (`hm_result["raw_content"]`) before the filter at lines 211-217 strips fakes. The filter throws out anything with `"former"` in the title or a fake-name match — it's possible the model IS returning names and the filter is eating all of them.

**Likely causes, in decreasing order of probability**

A. **The filter is too aggressive.** `_KNOWN_FAKE_NAMES` or the `"former"` substring match is catching legitimate entries. Log the pre-filter response for a couple of runs and confirm.
B. **The HM prompt is asking the wrong question.** Category blocks in [`category_blocks.py`](../src/vacancysoft/intelligence/prompts/category_blocks.py) have `hm_search_queries` like `"[company]" "head of credit" site:linkedin.com/in`. If LinkedIn has changed its public-profile visibility (or the exact phrasing in titles has drifted), the web search returns too little to extract names from.
C. **`search_context_size = "high"` for HM specifically** means OpenAI pulls ~100k of web context. If LinkedIn results aren't in that context (because they rank low or are blocked), the model has nothing to extract.

**Fix paths once a cause is confirmed**

- If (A): relax the filter to log-only until we know the false-positive rate.
- If (B): rewrite the HM prompts to be more tolerant of unclear matches — return "confidence: low" entries instead of nothing, so at least the operator has a starting name.
- If (C): try `search_context_size = "medium"` or `"low"` — less context but more targeted, and the model may pick up on LinkedIn hits that currently drown in bulk.

**Why P3**: hiring_managers is one of the most-used dossier fields (recruiters click on the HM first when opening the Campaign Builder). If it's systematically empty, the dossier has lost one of its most valuable outputs, and the money spent on the HM call is being wasted. Fix is likely small — the diagnostic alone may point at a one-line change.

---

### Ticket 15 — Fix `'NoneType' object has no attribute 'lower'` crash in intelligence client [P3]

**Context**: surfaced 2026-04-19 while diagnosing why the UOB lead appeared stuck. Two ARQ `process_lead` jobs for the same item failed after running for 141-196s with the error `'NoneType' object has no attribute 'lower'`. The worker's `try/except` in `process_lead` reverted the item's status to `pending` and let ARQ mark the job failed — which left the queue item looking stuck until the next self-heal sweep re-enqueued it.

**Suspect call sites** (from `grep -rn "\.lower()" src/vacancysoft/intelligence/`):

- `src/vacancysoft/intelligence/client.py:96` — `model_lower = model.lower()` in `_call_completions_api`. If `model` arg is `None`, this crashes.
- `src/vacancysoft/intelligence/client.py:156` — same pattern in `_call_responses_api`.

**Likely cause**: `configs/app.toml::[intelligence]` has `dossier_model` / `campaign_model` / `hm_search_model` set, but `_load_intel_config()` reads those via `config.get("dossier_model", "gpt-4o")` — defaults are strings, never None. So the None must be coming from a different path. Possible culprit: one of the DeepSeek-era config-key lookups (`config.get("dossier_model_deepseek")`) that returns None if the key isn't present and the toggle logic falls through in an unexpected way. Worth re-reading `dossier.py` and `campaign.py` for any `model=None` call-sites.

**Fix pattern**: wrap both `model.lower()` calls in `(model or "").lower()`. Zero-risk guard; covers the None path with a meaningful error later (`_resolve_rates` of empty string returns DEFAULT_PRICE; the actual OpenAI call with `model=""` raises a clearer `BadRequestError`).

**Diagnostic**: log the full request payload at DEBUG level just before the `client.chat.completions.create(...)` / `client.responses.create(...)` call. Next time a run fails, we'll see exactly what `model` was passed.

**Why P3**: users saw leads look stuck for 30+ minutes before the self-heal caught them. Fix is a one-line guard + a diagnostic. Low effort, high clarity.
