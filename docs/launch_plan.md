# Prospero — consolidated launch plan

**Status as of:** 2026-04-20
**Target:** soft launch to 3 internal users (recruiters), behind Entra ID
auth, on Azure Container Apps. See [deployment_plan.md](deployment_plan.md)
for the topology and [runbook_update_adapter.md](runbook_update_adapter.md)
for the post-deploy hot-path.

This document is the authoritative tracker of what stands between today
and the soft launch. Items are sourced from this conversation's
discussions, the original [TODO.md](TODO.md) (deeper per-ticket rationale
still lives there), and auto-memory notes.

Three buckets:

1. **Pre-launch must-haves** — without these, can't deploy or can't safely operate
2. **Pre-launch nice-to-haves** — would improve launch quality but won't block it
3. **Post-launch & ongoing** — defer, sequenced after the soft launch

---

## Bucket 1 — Pre-launch must-haves

### 1A. External dependencies (operator + admin actions)

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Set OpenAI hard + soft spend cap at [platform.openai.com/account/limits](https://platform.openai.com/account/limits) | You | ☐ TODO (~2 min) |
| 2 | Confirm tenant admin approves Entra app reg with `Mail.Send` + `Mail.Read` application permissions | You + IT | ☐ Email sent, awaiting response |
| 3 | Once approved: create Entra app reg, add both permissions, admin consent | You + IT | ☐ Blocked on #2 |
| 4 | PowerShell `New-ApplicationAccessPolicy` scoping the app to a `prospero-users` Entra group containing only your 3 launch users | You / Exchange admin | ☐ Blocked on #3 |
| 5 | Decide on launch domain (e.g. `prospero.<corp>.com`) and have it ready to point at Container Apps ingress | You | ☐ TODO |
| 6 | Rotate **all** keys currently in local `.env` (OpenAI, SerpApi, Adzuna, Reed, Coresignal, DeepSeek, webhook) before they go anywhere remote | You | ☐ TODO |

### 1B. Code-side completed already

| # | Item | Status |
|---|---|---|
| C1 | Sub-specialism as first-class DB column (migration 0007), retag all keywords, frontend reads from DB | ✅ commits 8ce17d4 / f6d2abb |
| C2 | Campaign Builder layout — Step labels, single-thread subject, verified HM email field | ✅ commit 520087e |
| C3 | Grey out unfinished UI surfaces behind FEATURES flags | ✅ commit c64d49b |
| C4 | Make `POST /api/agency` container-safe (drop `git add` subprocess); add `prospero agency add` CLI | ✅ commit 5b74cc8 |
| C5 | Deployment plan + adapter-update runbook committed to docs/ | ✅ commit b96ac27 |
| C6 | CI on push/PR (pytest + ruff + tsc) | ✅ TODO ticket 1, commit 34d0561 |
| C7 | Kill `models_v2.py` legacy naming | ✅ TODO ticket 5, commit f1b2456 |
| C8 | xfail Pricing Actuary test | ✅ TODO ticket 6, commit 163d8e9 |
| C9 | Lever adapter robustness: derive slug from URL; bump timeout 20s→60s | ✅ commit 6815c02 (PR #4) |
| C10 | Pipeline stall fix: `NOT IN` → `NOT EXISTS`; scope enrich/classify/score to `--adapter` | ✅ commit 7e585ea (PR #5) |
| C11 | Persist source-level discovery failures as SourceRun + ExtractionAttempt rows | ✅ commit b81f29a (PR #6) |
| C12 | Remove N8N webhook integration (delete webhook_sender.py + CLI commands + configs + envs) | ✅ commit 6116252 (PR #7) |
| C13 | Lever data cleanup: 103 mis-classified lever rows triaged and allocated — 1→bamboohr (Walker Crips), 7→generic_site (Vanquis, Verition, Voleon, West Brom ×2, Yieldstreet, York Capital), 95 deactivated as duplicates (active generic_site twin already handles each). Backup at `.data/backups/sources_pre_lever_cleanup_20260420-1945.sql`. Active lever rows now 112 → 11, all with valid `jobs.lever.co/*` URLs | ✅ DB-only (pure SQL UPDATE, no code change) |

### 1C. Email + scheduling tranche (~13–14 hours focused work, blocked on 1A items 2-4)

The single biggest piece of remaining work. Without this, the app is read-only and there's no point inviting colleagues. Sequence below assumes Decision: Microsoft Graph application permission, ARQ deferred jobs for scheduling, inbox polling for reply detection.

| # | Item | Effort | Notes |
|---|---|---|---|
| E1 | Migration 0008: `campaign_schedules` + `campaign_sends` + `campaign_events` tables | 30 min | Schema in [deployment_plan.md](deployment_plan.md) under "Email functionality" |
| E2 | Graph SDK wrapper: `send_via_graph(user_email, subject, body_html, in_reply_to=None)` | 1 h | Includes `In-Reply-To` + `References` headers for Outlook threading |
| E3 | Tracking pixel endpoint `GET /api/track/open/{pixel_id}.gif` (must bypass Easy Auth in prod via ingress carve-out) | 45 min | Returns 1x1 transparent GIF; updates `campaign_sends.first_opened_at` + `open_count` |
| E4 | ARQ worker function `send_campaign_step(schedule_id, step_num)` | 2 h | Loads schedule, bails if `status != active`, sends, writes `campaign_sends`, enqueues step N+1 with `_defer_by=wait_days[N]` |
| E5 | Just-in-time inbox check before each send (catches replies that arrived between polls) | 30 min | Targeted `GET /messages?$filter=...` for the last hour |
| E6 | `POST /api/campaigns/schedule` endpoint (called by Launch Campaign button) | 1 h | Creates schedule row, enqueues step 1 |
| E7 | `POST /api/campaigns/schedule/{id}/cancel` endpoint | 30 min | Trivial status flip |
| E8 | Builder UI: wire "Launch Campaign" button + add Cancel link | 30 min | Includes confirmation modal showing the computed cadence |
| E9 | ARQ cron `poll_replies` — every 10 min, per user, reply + bounce matching by `In-Reply-To` header | 2 h | Marks schedules as `replied` / `bounced`; future steps bail on next execution |
| E10 | Auto-reply / OOO heuristic filter (`Auto-Submitted: auto-replied`, subject regex) — don't kill campaigns on OOO messages | 30 min | Classify as `auto_reply` event but leave schedule status alone |
| E11 | Flip `FEATURES.campaignLaunch = true` in `web/src/app/lib/features.ts` | 1 min | Re-enables the Launch button |
| E12 | End-to-end smoke test locally: send to Gmail, reply from Gmail, confirm step 2 does NOT fire | 30 min | Launch-critical |
| E13 | Add unsubscribe link + privacy notice to email template footer (UK GDPR for unsolicited B2B) | 30 min | Talk to legal before launch wording. The link's `href` targets the endpoint built in E14. |
| E14 | **Unsubscribe mechanism + blacklist** — signed-token landing URL, `campaign_blacklist` table, send-time check that skips blacklisted recipients + cancels remaining steps | 2–3 h | Migration `0009_add_campaign_blacklist` (columns: `email`, `reason`, `unsubscribed_at`, `source_enum`, `schedule_id?`). New route `GET /api/unsubscribe/{token}` validates an HMAC-signed token embedded in each email (prevents spoofing), writes the blacklist row, cancels the originating schedule's remaining steps, returns a minimal confirmation page with the same Easy-Auth carve-out as the tracking pixel. `send_campaign_step` checks `campaign_blacklist.email = recipient_email` before every send and bails if present. **Launch-blocker: a deliverability bug or legal complaint from a missing opt-out is existential for the service.** |

### 1D. Infra (Bicep template + first deploy)

The Bicep skeleton is in the worktree at `infra/main.bicep` but resources are currently `TODO(phase 3)` stubs. Need to be filled in.

| # | Item | Effort | Notes |
|---|---|---|---|
| I1 | Complete Bicep resource definitions (Log Analytics, App Insights, ACR, Key Vault, Postgres Flexible Server, Redis Cache, Container Apps Environment, three Container Apps, two Jobs) | 4–6 h | Iteration expected; start simple, scale up |
| I2 | VNet + private endpoints for Postgres + Redis | 1–2 h | Sharp edges around subnet sizing; budget extra |
| I3 | Easy Auth configuration on `ca-api` AND `ca-web` (verify both, not just one) | 30 min | Mis-applying this is the silent-leak risk |
| I4 | Tracking-pixel ingress carve-out (`unauthenticatedAction: Allow` for `/api/track/open/*`) | 15 min | Recipients aren't in your tenant |
| I5 | KEDA scaler on `ca-worker` watching Redis list length | 30 min | Avoids paying for idle worker compute |
| I6 | Container Apps Job `job-migrate` (one-shot, blocks deploy until exit 0) | 30 min | Already drafted in deploy.yml workflow |
| I7 | Container Apps Job `job-scrape-cron` (hourly trigger; enqueues scrape jobs) | 30 min | Skip for stage; enable for prod |
| I8 | Build all four Dockerfiles (api, migrate, worker, web) | 1–2 h | Skeletons in deployment_plan.md |
| I9 | Stand up `rg-prospero-stage` resource group | 1 h | First deploy will surprise you in some way; budget for it |
| I10 | Run full deploy workflow against staging; fix what breaks | 0.5–1 day | Practice the full path |
| I11 | Practice rollback in staging (deliberately deploy a known-bad image, roll back) | 30 min | Don't learn rollback during an incident |
| I12 | Stand up `rg-prospero-prod` resource group | 0.5 h | Same Bicep, different parameters file |
| I13 | First prod deploy + smoke test (`/api/stats` returns 200 behind Entra ID redirect) | 0.5 day | |

### 1E. Operational stability fixes

These are real bugs that will bite a small user base in week 1 if not addressed:

| # | Item | Source | Effort |
|---|---|---|---|
| O1 | Fix `'NoneType' object has no attribute 'lower'` crash in intelligence client (`client.py:96`, `client.py:156`) — wraps `model.lower()` calls in `(model or "").lower()` | TODO ticket 15 | 30 min |
| O2 | Set up `AGENCY_EXCLUSIONS_PATH` env var to point at Azure Files mount in prod, so runtime `mark_agency` calls survive container restarts | This session | 15 min Bicep edit |
| O3 | **Seed reactivates deactivated sources.** Observed 2026-04-20: after manually setting `active=false` on `lever_the_hartford_a23e1dc2` and `lever_vaneck_e5f37328` (both mis-classified Lever sources pointing at non-`jobs.lever.co` URLs), a subsequent `prospero pipeline run --adapter lever` flipped both back to `active=true` at the same `updated_at` timestamp (18:45:37 on 2026-04-20). Source: `seed_sources_from_config()` in [source_registry/config_seed_loader.py](src/vacancysoft/source_registry/config_seed_loader.py) is called implicitly at pipeline-run time (or is being called by some other startup hook) and re-asserts `active=true` on every row it finds in the seed config, including the broken ones. Fix: either (a) update the seeder to respect an existing `active=false` rather than overwriting; (b) remove the 101 mis-classified Lever entries from the seed config files entirely; or (c) add a `seed_type='do_not_seed'` sentinel on manually-deactivated rows. Without the fix, bug 1-style failures will regenerate on every seed refresh and need re-deactivating. | This session | 30 min code + seed-config review |

---

## Bucket 2 — Pre-launch nice-to-haves

Would improve launch quality but won't block. Pick off opportunistically if email/infra work runs ahead of schedule.

### 2A. Cost + safety guardrails

| # | Item | Source | Effort |
|---|---|---|---|
| N1 | Application Insights alert on per-hour OpenAI spend over £X — fires email when threshold breached | This session | 30 min Portal config |
| N2 | Azure budget alerts on the subscription itself (covers infra cost, not OpenAI) | This session | 15 min Portal config |
| N3 | Extend Azure Postgres Flexible Server auto-backups from default 7 days → 35 days | deployment_plan.md | 5 min Portal flip |

### 2B. Pre-launch polish

| # | Item | Source | Effort |
|---|---|---|---|
| N4 | Backfill xlsx Scraping Rules sub-spec column for non-Quant categories (today they show `None`; the Python rules have the data) | This session | 15 min |
| N5 | Decide: keep `Investment Risk` sub-spec as empty placeholder or remove entirely | This session | 5 min |
| N6 | Front Office sub-spec review (13 retaggings skipped during the 2026-04-20 retag) | This session | 1 h |
| N7 | Update `configs/legacy_routing.yaml` to mirror the new sub-spec set, OR leave + accept exporter drift | This session | 30 min if applied |
| N8 | Header bell / cog / global-search icons: dim further or stub with `cursor: default` (currently visually muted but still look interactive) | This session | 5 min |
| N9 | Pull user name + initials from Entra ID `/.auth/me` claim instead of hardcoded "Antony B." in [Sidebar.tsx](web/src/app/components/Sidebar.tsx) | deployment_plan.md follow-up | 30 min |

### 2C. Tickets from earlier sessions (TODO.md) — small + worth landing pre-launch

| # | Item | Source | Effort |
|---|---|---|---|
| N10 | Migrate `@app.on_event` startup/shutdown to `lifespan` (kill FastAPI deprecation noise) | TODO ticket 4 | 1 h |
| N11 | Investigate why HM search returned zero `hiring_managers` (P3 — recruiters click HM first; if always empty, dossier loses its most-used field) | TODO ticket 14 | 1–2 h |
| N12 | Worker-side cache invalidation after `scrape_source` (Redis-shared signal so /api/sources rebuilds without 30s TTL wait) | TODO ticket 3 | 30 min if simple, 2 h if Redis-shared |

### 2D. Hardening of agency exclusion (proper version)

| # | Item | Source | Effort |
|---|---|---|---|
| N13 | Replace YAML-write-on-API-call with a `config_change_requests` table + `prospero config apply-pending` CLI that opens a PR | This session | 2–3 h |

The minimal version (CLI-only) shipped in commit 5b74cc8; this is the durable version once we want non-operator users to mark agencies via the UI.

---

## Bucket 3 — Post-launch & ongoing

In rough order of when they're likely to land. Sequence after the soft launch is stable.

### 3A. Week 1–2 after launch (operational must-haves)

| # | Item | Source |
|---|---|---|
| P1 | Watch first real campaigns end-to-end; gather user feedback on send cadence + tone | — |
| P2 | Add tighter Cost-per-lead cost report visualisation; the data is in `IntelligenceDossier.cost_usd` + `CampaignOutput.cost_usd` already | This session item 1 |
| P3 | Email templates iteration based on real reply rates | — |

### 3B. Week 2–4 (admin page, item 1 from original list)

| # | Item | Source |
|---|---|---|
| P4 | `/admin` page with cost rollup, throughput, performance, source health | This session item 1 |
| P5 | New endpoints `/api/admin/costs?since=&group_by=`, `/api/admin/throughput`, `/api/admin/performance`, `/api/admin/source-health` (most data already in DB) | This session item 1 |
| P6 | Application Insights deep-link button from `/admin` page so logs are one click away | This session item 1 |

### 3C. Month 2 — Campaign Manager (item 3 from original list)

| # | Item | Source |
|---|---|---|
| P7 | Real `/campaigns` page: list view of `campaign_schedules` with computed Step / Status / Opens / Last Activity columns | This session item 3 |
| P8 | Per-row drawer: full email history + events for that schedule | This session item 3 |
| P9 | Manual Mark-as-Replied / Mark-as-Meeting-Booked buttons (auto-reply detection already running from launch; this catches the ones polling missed) | This session item 3 |
| P10 | Top-bar stats: Emails Sent / Open Rate / Reply Rate / Meetings Booked / Bounce Rate aggregated from `campaign_sends` + `campaign_events` | This session item 3 |
| P11 | Filter chips: All / In Sequence / Replied / Meeting Booked / No Response | This session item 3 |
| P12 | Flip `FEATURES.campaignsManager = true` in `web/src/app/lib/features.ts`; remove "Preview only" banner from /campaigns page | This session |

### 3D. Month 2–3 — Campaign Builder enhancements

| # | Item | Source |
|---|---|---|
| P13 | Save Draft persistence (currently state-only, lost on refresh). New `campaign_drafts` column on `ReviewQueueItem` or its own table. Flip `FEATURES.campaignSaveDraft = true` | This session |
| P14 | Per-step preview before send (review the content for steps 2-5 before they fire) | This session |
| P15 | Pause / Resume distinct from Cancel | This session |
| P16 | Per-lead HM override in Builder (currently only the default source is choosable; this lets the operator set HM email per-campaign) | This session |
| P17 | Reply detection v2: Graph change-notification webhooks for seconds-latency reply tracking. Polling stays as the fallback. | This session |

### 3E. Month 3+ — Settings pages

| # | Item | Flag to flip when shipped |
|---|---|---|
| P18 | `/settings/scoring` page: tweak rule weights via UI | `FEATURES.scoringRules` |
| P19 | `/settings/integrations` page: API keys + connector status | `FEATURES.integrations` |
| P20 | `/settings/team` page: user list + permissions | `FEATURES.team` |

### 3F. Multi-user system (the big one)

Required for: per-user campaign-voice learning, real audit trails, per-user cost attribution, anything beyond the soft-launch 3 users.

| # | Item | Source |
|---|---|---|
| P21 | `users` table + auth threading; map Entra ID claims to Prospero user rows | Memory note: "multi-user planned for later" |
| P22 | Tenant-scoping for all DB queries (campaigns belong to a user, sources can be global vs per-user, etc.) | — |
| P23 | Per-user campaign-voice few-shot learning: store user edits, retrieve top N as in-context examples | TODO: per-user voice ticket |
| P24 | Cross-user dossier sharing decision (cheaper) vs per-user dossiers (more personalised) | — |
| P25 | Move from Application permission to Delegated for Graph send (better blast-radius story for multi-user) | This session |

### 3G. Backlog from TODO.md (preserved for context; rationale lives in [TODO.md](TODO.md))

Each is independently doable; ordered by likely value:

| # | TODO ticket | Priority (TODO.md) | Effort |
|---|---|---|---|
| P26 | Surface zero-classification direct sources on Sources page ("No Jobs Found" tab is permanently empty) | Deferred ticket | 30–60 min |
| P27 | Dedupe `_extract_employer_from_payload` (3-site drift causes Reed/Adzuna employer-display bugs) | Deferred ticket | 15 min |
| P28 | Shared `_canonical_employer_norm()` helper across 3 ledger sites (suffix-mismatch bucketing bug) | TODO ticket 2 | 30 min |
| P29 | Promote sources-page derived helpers into `web/src/app/sources/utils.ts` | TODO ticket 7 | 20 min |
| P30 | Add `response_model=` to the 9 dict-returning handlers (better OpenAPI docs) | TODO ticket 8 | 45 min |
| P31 | Per-company aggregator probe for stronger "No Jobs Found" signal | TODO ticket 9 | 2–3 h |
| P32 | Fold the filter-label / "Clear filter" block into `StatsSection` | TODO ticket 10 | 10 min |
| P33 | Delete `_addcompany_count_jobs` if confirmed unused | TODO ticket 11 | 5 min |
| P34 | Composite index on `SourceRun(source_id, created_at)` if profiling shows hot path | TODO ticket 12 | 15 min conditional |
| P35 | Auto-fix failed scrapes + diagnose button on source cards | Memory: priorities_next_session | TBD |
| P36 | Paste-dedupe fuzzy: `/api/leads/paste` only dedupes on exact URL, creates duplicates when pasting ATS URL for an already-aggregator-scraped lead | Memory: paste_dedupe_fuzzy | TBD |

### 3H. Deprioritised / probably never

| # | Item | Decision |
|---|---|---|
| P37 | Investigate DeepSeek for dossier + campaign properly | TODO ticket 13 — two runs confirmed DeepSeek doesn't match OpenAI quality on this pipeline. Stay on OpenAI. Re-engage only if DeepSeek ships web-search support. |

---

## Sequencing — what to do next, in order

Assuming you're starting fresh tomorrow:

| Day | Work | Blocks on |
|---|---|---|
| 0 (today) | Set OpenAI spend cap (item 1A.1) | — |
| 1 AM | Reply from tenant admin re: Mail.Send + Mail.Read | external |
| 1 PM | Once admin OK'd: create Entra app reg + Application Access Policy (1A.3 + 1A.4) | 1A.2 |
| 2 | Bicep template — fill in TODO stubs (I1) | — |
| 3 | Email work: E1 (migration), E2 (Graph wrapper), E3 (tracking pixel), E4 (worker function) | Entra app reg |
| 4 | Email work: E5 (just-in-time check), E6 (schedule endpoint), E7 (cancel), E8 (Builder wiring) | Day 3 |
| 5 AM | Email work: E9 (poll_replies cron), E10 (auto-reply filter) | Day 4 |
| 5 PM | E14 (unsubscribe mechanism + blacklist) | E4 + E13 wording |
| 6 AM | E13 (unsubscribe link + privacy notice in footer), E12 (smoke test), E11 (flip FEATURES flag) | E14, legal OK |
| 6 PM | I8 (Dockerfiles), I9 (staging RG) | I1 |
| 7 | I10 (deploy to staging), I11 (rollback drill), O1 + O2 (stability fixes) | Day 6 PM |
| 8 AM | I12 + I13 (prod RG + first prod deploy) | Day 7 green |
| 8 PM | Invite 3 colleagues; watch first real campaigns; iterate on what breaks | — |

**Total: ~1.5–2 working weeks from today** (assuming admin approval lands by day 1 AM; legal wording on E13 cleared by day 5).

---

## How to maintain this document

- Tick items off as they land. Reference the commit hash in the relevant row.
- When a new tier-1 blocker is discovered, add it to Bucket 1 with effort estimate and source.
- Weekly: prune Bucket 2 items that have decayed in priority; demote to Bucket 3.
- After launch: archive Bucket 1 entirely; the "what's next" list lives in Bucket 3.

The deeper rationale for each pre-launch ticket from earlier sessions
remains in [TODO.md](TODO.md) — that file is unchanged. This document
supersedes TODO.md as the **forward planning** view; TODO.md is now
the **reference / decision log** view.
