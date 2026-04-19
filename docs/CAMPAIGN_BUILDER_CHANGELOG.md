# Campaign Builder Change Log

Chronological log of changes made during the Campaign Builder wiring work.
Use this to roll back any change by reverting to the prior behaviour described.

---

## 2026-04-17 — Rewire `web/src/app/builder/page.tsx` to load campaigns from API

**File touched:** `web/src/app/builder/page.tsx`

**Change summary:**
- Converted the page to stateful: introduced `useState` for the 5-step sequence and the active step.
- Added a lead selector in the top settings bar that fetches `GET /api/queue` and filters to `status === "ready"` leads.
- Reads `?lead=<id>` from the URL via `useSearchParams`; selecting a lead updates the URL.
- When a lead is active, POSTs to `/api/leads/{id}/campaign` and populates step `subject` + `body` from the returned `emails` array.
- The Sequence Steps column now drives the active-step purple border from React state; clicking a step updates `activeStep`.
- The Preview panel now renders the subject as an `<input>` and the body as a `<textarea>` bound to the active step. Edits live only in component state (no persistence endpoint yet — `// TODO: persist edits` left in source).
- Hiring Manager Source card, tone selectors, wait-days selectors, and step titles/days left untouched.

**Previous behaviour (rollback target):**
The page was a static mockup. `steps` was a hard-coded module-level array; Step 1 was always highlighted; the preview panel rendered fixed JSX with a pre-written email template for Step 1 only. No data fetching occurred.

**To roll back:** `git checkout HEAD -- web/src/app/builder/page.tsx` (prior to this session's first commit) or restore from the version at commit `79cc262` or earlier.

---

## 2026-04-17 — Re-applied pre-gen (Tier 3, quota restored)

**Files touched:**
- `src/vacancysoft/worker/tasks.py` — re-added the campaign pre-gen block after dossier
- `src/vacancysoft/api/server.py` — same for the in-process fallback

**Why:** User is now on OpenAI Tier 3 (no rate-limit risk at projected 25-user × 3-5 leads/day volume) and billing quota is restored (confirmed via direct API ping). Pre-gen is safe again. Same wrapped `try/except` so a single failed campaign doesn't block the dossier from landing.

**Previous (current) behaviour pre-change:** Dossier-only in worker; Builder hit `POST /api/leads/{id}/campaign` lazily on open (60-90s wait per lead).

**To roll back again:** same instructions as the "Reverted pre-gen" entry below.

---

## 2026-04-17 — Reverted pre-gen (OpenAI quota outage)

**Files touched:**
- `src/vacancysoft/worker/tasks.py` — removed the `generate_campaign` block
- `src/vacancysoft/api/server.py` — removed the matching block in the in-process fallback

**Why:** During testing we hit an OpenAI `insufficient_quota` 429. Dossier generation was failing at line 95 of `worker/tasks.py`, never reaching the campaign step. Because `process_lead` rolls the queue status back to `pending` on any exception, leads appeared to bounce `pending → generating → pending` repeatedly. Reverting pre-gen is unrelated to fixing the quota issue, but it reduces the surface area while billing is sorted; once quota is healthy, pre-gen can be re-added from the changelog entry below.

**To re-apply later:** see next entry; re-add the `try: generate_campaign(...) except` blocks in both files.

---

## 2026-04-17 — Pre-generate campaigns in the worker

**Files touched:**
- `src/vacancysoft/worker/tasks.py` (`process_lead`)
- `src/vacancysoft/api/server.py` (`_scrape_and_generate_dossier` — in-process fallback when Redis isn't running)

**Change summary:**
After the worker finishes a dossier, it now immediately calls `generate_campaign(dossier.id, s)` before marking the lead `ready`. Result: when the user opens the Campaign Builder and picks the lead, the `POST /api/leads/{id}/campaign` call hits the `CampaignOutput` cache and returns in ~10ms instead of waiting ~60-90s for gpt-5.2. Same code path added to the API-process fallback that runs when the Redis worker is unavailable, so behaviour is consistent.

The pre-generation is wrapped in `try/except` — if gpt-5.2 fails for any reason, the lead still becomes `ready` (dossier is still useful) and the Builder regenerates on demand. No functional regression vs prior behaviour.

The queue-status gate (`GET /api/queue` downgrade to "generating" when no dossier) was NOT changed to also require a campaign. Reason: the Leads page should surface the dossier as soon as it's done; making `ready` wait for the full campaign would delay that UX by ~60-90s for no benefit. The Builder's on-demand generation still works as a fallback for the rare case where a user clicks in fast.

**Previous behaviour (rollback target):**
Worker marked leads `ready` immediately after dossier generation. Campaign generation happened lazily on first Builder load (~60-90s user-visible wait).

**To roll back:**
- In `worker/tasks.py`, remove the `try: ... generate_campaign(dossier.id, s) ... except` block between the dossier log line and the `item.status = "ready"` block.
- In `api/server.py`, the same block inside `_scrape_and_generate_dossier`.

---

## 2026-04-17 — Revert: campaign_model back to gpt-5.2

**Files touched:**
- `configs/app.toml` (`campaign_model` gpt-4o → gpt-5.2)
- Database: all 8 cached `campaign_outputs` rows deleted so the next generation regenerates against gpt-5.2.

**Why:** gpt-4o output quality judged unacceptable by the user. Accepting the ~60–90s latency to keep gpt-5.2's output quality.

---

## 2026-04-17 — Split models: gpt-5.2 for dossiers, gpt-4o for campaigns

**Files touched:**
- `configs/app.toml`
- Database: all existing `campaign_outputs` rows deleted (16 rows) so cached gpt-5.2 campaigns don't shadow fresh gpt-4o ones.

**Change summary:**
- `intelligence.campaign_model` changed from `gpt-5.2` → `gpt-4o`.
- `intelligence.dossier_model` stays on `gpt-5.2` (user wants the slower, deeper reasoning for the intelligence report and hiring-manager research).
- Motivation: the campaign prompt now asks for 5 emails × 6 tone variants = 30 complete emails in one response. On gpt-5.2 (reasoning model), that was ~66s per first generation. gpt-4o (non-reasoning) produces the same payload in ~34s, roughly half the time. Quality trade-off is acceptable for campaign copy; dossier quality isn't being touched.
- Because the campaign endpoint caches its output per dossier in `campaign_outputs.raw_response`, purged the table so every lead regenerates against gpt-4o on next fetch rather than returning the old gpt-5.2 cache.

**Previous behaviour (rollback target):**
Both `dossier_model` and `campaign_model` were set to `gpt-5.2`. Campaign generation took ~60–90s. 16 cached campaign outputs existed.

**To roll back:** set `campaign_model = "gpt-5.2"` in `configs/app.toml` and restart the API. Cached campaigns cannot be restored; they'll regenerate on demand.

---

## 2026-04-17 — Gate queue "ready" status on dossier presence

**File touched:** `src/vacancysoft/api/server.py` (`list_queue` / `GET /api/queue`)

**Change summary:**
`GET /api/queue` now downgrades a stored `status = "ready"` to `"generating"` in the response when no `IntelligenceDossier` row is reachable for the lead's URL. The DB column is not modified — only the reported status. This protects the Builder (and Leads list) from seeing a lead as "ready" before its dossier has actually been persisted, which was causing the Campaign Builder to fire `POST /api/leads/{id}/campaign` too early and get back `400 "Generate a dossier first"`.

Implementation detail: the check joins `raw_jobs → enriched_jobs → intelligence_dossiers` keyed by `raw_jobs.discovered_url`, then does set membership against each queue item's URL. We match on URL (not on `ReviewQueueItem.enriched_job_id`) because multiple `EnrichedJob` rows can exist for a single URL and the queue item's `enriched_job_id` may point at a different one than the dossier's — observed in practice. One extra query per `/api/queue` call; no N+1.

**Previous behaviour (rollback target):**
`list_queue` returned whatever status was in the `review_queue_items.status` column directly.

**To roll back:** restore the original single-line dict comprehension (`"status": item.status`) and drop the `urls_with_dossier` pre-fetch.

---

## 2026-04-17 — Tone change also activates the step

**File touched:** `web/src/app/builder/page.tsx` (function `updateStepTone`)

**Change summary:**
Picking a tone from a step's dropdown now also calls `setActiveStep(stepNum)`. Previously the preview only reflected the new variant when the user was already on that step; changing tone on any other step updated state silently while the preview stayed pinned to the active step. This made it look like only Step 1's tones cycled.

**Previous behaviour (rollback target):**
`updateStepTone` only mutated `steps`; activation was purely driven by the outer card click, which the dropdown blocked via `stopPropagation`.

**To roll back:** remove the trailing `setActiveStep(stepNum);` line in `updateStepTone`.

---

## 2026-04-17 — 6 tone variants per email + tone-driven preview

**Files touched:**
- `src/vacancysoft/intelligence/prompts/base_campaign.py`
- `configs/app.toml`
- `web/src/app/builder/page.tsx`
- Database: all existing `campaign_outputs` rows deleted (4 rows).
- Queue: all `review_queue_items` of type `campaign` deleted (15 rows) via `DELETE /api/queue/{id}`.

**Change summary:**
- **Prompt rewrite:** `CAMPAIGN_TEMPLATE` now asks ChatGPT to return six tone variants for each of the five sequence steps (`formal`, `informal`, `consultative`, `direct`, `candidate_spec`, `technical`) with tone definitions per variant. Output JSON shape changed from `{emails:[{sequence, subject, body}]}` to `{emails:[{sequence, variants:{formal:{subject,body}, ...}}]}`.
- **Token budget:** `intelligence.max_tokens` in `configs/app.toml` raised from 8000 → 16000 to accommodate the 6× expanded output.
- **Builder page:** state model now holds `variants: Record<tone, {subject,body}>` per step. The per-step tone dropdown (formerly cosmetic) now drives which variant is rendered in the preview; changing it swaps subject + body live. Edits still stay in local state (no persistence yet).
- **Back-compat:** the builder has a fallback — if the API returns an old-shape email (`{subject, body}` at top level), it loads the text into the step's default tone slot so the preview still shows something.
- **Data purge:** cleared cached `CampaignOutput` rows and the queue so the next lead triggers a regeneration against the new prompt instead of returning a stale 1-variant cache.

**Previous behaviour (rollback target):**
- `base_campaign.py` asked for a single subject/body per email.
- `max_tokens` was 8000.
- `builder/page.tsx` stored one subject + one body per step; the tone dropdown was purely cosmetic.
- 15 ready leads were sitting in the queue; 4 campaign outputs were cached in the DB.

**To roll back:**
- `git checkout HEAD -- src/vacancysoft/intelligence/prompts/base_campaign.py configs/app.toml web/src/app/builder/page.tsx`
- Queue and campaign cache cannot be restored by code revert; user will need to re-queue leads to regenerate.

---

## 2026-04-17 — Fix campaign 500/503 for gpt-5 reasoning models

**File touched:** `src/vacancysoft/intelligence/client.py`

**Change summary:**
- `_call_completions_api` now detects reasoning-family models (`gpt-5*`, `o1*`, `o3*`, `o4*`) and sends `max_completion_tokens` instead of `max_tokens`. For reasoning models it also omits `temperature` (those models only accept the default). Non-reasoning models (e.g. `gpt-4o`) continue to use `max_tokens` + `temperature` unchanged.
- Motivation: the campaign config had been moved to `gpt-5.2` in `configs/app.toml` (better output quality per the user), but OpenAI returns HTTP 400 for `max_tokens` on gpt-5. That was surfacing as 500/503 from `POST /api/leads/{id}/campaign` and blocking the Builder page from ever loading data.

**Previous behaviour (rollback target):**
`_call_completions_api` unconditionally sent `max_tokens` + `temperature`. Worked for `gpt-4o`, broke for `gpt-5*`.

**To roll back:** `git checkout HEAD -- src/vacancysoft/intelligence/client.py` or restore lines 82-88 to the original single kwargs dict.

---
