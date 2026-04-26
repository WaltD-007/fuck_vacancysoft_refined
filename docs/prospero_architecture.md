# Prospero — Master Architecture Document (Technical)

**Audience**: New developers joining the project, AI assistants picking up cold context, technical operators.
**Last updated**: 2026-04-26.
**Companion**: `docs/prospero_overview.md` (non-technical version).
**Sources**: This doc consolidates four codebase audits (saved in `/tmp/audit_*.md`) plus the active plans under `~/.claude/plans/`.

> Naming note: the codebase still uses `vacancysoft` as the Python package name. The product name is **Prospero**. A rename is planned (`~/.claude/plans/rename-vacancysoft-to-prospero.md`). Treat the two as synonyms in this doc.

---

## 1. TL;DR

Prospero is a recruitment-intelligence platform for an executive-search agency. It performs four jobs end-to-end:

1. **Discover** open finance/risk/quant/compliance/audit/cyber/legal jobs by scraping ~1,500 sources (35 ATS-specific adapters + a generic-site fallback).
2. **Filter and score** each job (geography, recruiter-vs-employer, title relevance, completeness) and decide if it's worth pursuing.
3. **Generate hiring intelligence** for accepted leads — an 8-section LLM dossier (company context, real problem, JD-vs-actual gaps, risks, candidate profiles, lead score, hiring-manager search) plus a 5-sequence × 6-tone outreach campaign personalised to the operator's voice.
4. **Send and track** the outreach via Microsoft Graph (5-email cadence at 0/7/14/21/28 days, auto-cancelled on detected reply).

The full product is **code-complete except for the live-send wiring**. Keybridge security approval has been received (2026-04-26). Remaining blockers are: Entra app registration, PR D (launch/cancel endpoints + UI button), PR E (Bicep IaC), the self-reply-filter Entra-GUID refactor, a production smoke test, and flipping the `OUTREACH_DRY_RUN` kill switch on launch day.

**Stack**: Python 3.13, FastAPI, SQLAlchemy + Alembic, PostgreSQL, ARQ + Redis, Playwright (Chromium + Firefox fallback), Next.js 16 (all client-side SWR), Microsoft Graph API. Hosted target: Azure Container Apps with Entra Easy Auth at the ingress.

**Cost envelope**: ~$0.135/lead for full intelligence (dossier + HM search + campaign).
**Scale today**: ~1,500 sources, ~120k enriched jobs, single-user dev mode.
**Scale target**: 25-30 BS recruiters single-tenant by ~June 2026, multi-tenant SaaS 12+ months out.

---

## 2. System map

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                            BROWSER (Next.js 16, SWR)                           │
│   Dashboard │ Sources │ Leads │ Campaigns │ Builder │ Settings/Voice           │
└──────────────────────────────┬─────────────────────────────────────────────────┘
                               │ HTTP /api/*
┌──────────────────────────────▼─────────────────────────────────────────────────┐
│                       FASTAPI (api/server.py, :8000)                           │
│  Routers: leads, sources, add_company, campaigns, users, voice                 │
│  In-process caches: dashboard, sources, ledger (30s TTL)                       │
└─────┬────────────────┬──────────────────────────────┬──────────────────────────┘
      │                │                              │
      │ enqueue        │ read/write                   │ read
      ▼                ▼                              │
┌──────────┐  ┌─────────────────────────────────┐    │
│  REDIS   │  │           POSTGRES              │    │
│  ARQ Q   │  │  19 tables; FK chain:           │    │
└────┬─────┘  │  Source → RawJob → EnrichedJob  │    │
     │        │   → Class+Score → Dossier       │    │
     │ pop    │   → Campaign → SentMessage      │    │
     ▼        │   → ReceivedReply               │    │
┌──────────┐  └─────────────┬───────────────────┘    │
│  WORKER  │                │                        │
│  ARQ pool│                │                        │
│  25 slots│                │                        │
└────┬─────┘                │                        │
     │ writes               │                        │
     ▼                      │                        │
┌────────────────────────────────────────────────────────────────────────────────┐
│                         BACKEND PIPELINE LAYERS                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ Adapters x35 │→ │  Pipeline    │→ │ Intelligence │→ │  Outreach        │    │
│  │ + generic    │  │ enrich/class │  │ dossier+camp │  │  Graph + ARQ     │    │
│  │ Playwright   │  │ filter+score │  │ voice layer  │  │  DRY_RUN switch  │    │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘    │
└────────────────────────────────────────────────────────────────────────────────┘

External services:
- OpenAI (default for dossier, campaign, HM search)
- DeepSeek (toggle for dossier and/or campaign)
- SerpApi (default for HM search since 2026-04, OpenAI fallback if it fails)
- Microsoft Graph (live-send pending Entra app registration; Keybridge approval received 2026-04-26)
- Playwright runner (Node microservice, separate Container App, for advert URL scrape)
```

**Key invariants**:
- Worker and API both write Postgres. Worker reads from ARQ; API never blocks on long jobs (everything LLM-heavy is enqueued).
- API caches are in-process module-level dicts (multi-replica gotcha — see §9.3).
- Full pipeline is restartable from any stage (`pipeline run --enrich-only`, `--classify-only`, etc.).
- Soft-delete is the default; hard-delete is rare (audit-corrections only).

---

## 3. The data lifecycle

A single trace from operator action to sent email, calling out every persistence boundary.

### 3.1 Discovery path (operator: paste a URL)

1. Operator opens **Sources** page → "Add Source" → pastes URL.
2. Frontend `POST /api/sources/detect` → `source_detector.detect_platform(url)` ([src/vacancysoft/api/source_detector.py:15](src/vacancysoft/api/source_detector.py)). Regex match against `PLATFORM_PATTERNS` (Workday, Greenhouse, etc.) returns adapter name + extracted config or `None` (= falls through to generic).
3. Frontend `POST /api/sources` → API inserts `Source` row, fingerprint dedup, enqueues ARQ `discover_source` job.
4. Worker pops job → instantiates adapter via `load_adapter_by_name()` → calls `adapter.discover(source_config)`.
5. Adapter returns `DiscoveryPage(records: list[DiscoveredJobRecord], diagnostics: AdapterDiagnostics)`.
6. Worker writes:
   - One `RawJob` per record (with `listing_payload` JSON, `content_hash` for global dedup).
   - One `SourceRun` row capturing `diagnostics_blob` (warnings, errors, counters, timings).

### 3.2 Discovery path (alternative: paste advert text)

1. Operator opens **Leads** page → "Paste advert" tab → pastes free-form advert text.
2. Frontend `POST /api/leads/paste` with `{text}` body.
3. API calls `extract_advert_fields(text)` ([src/vacancysoft/intelligence/advert_extraction.py:1](src/vacancysoft/intelligence/advert_extraction.py)) → gpt-4o-mini extracts `{title, company, location, postedDate}` deterministically (`temperature=0.0`).
4. API creates a synthetic `Source` (adapter='manual_paste') + `RawJob` + `EnrichedJob` in one transaction.
5. API enqueues lead for dossier/campaign generation via `process_lead` worker task.

### 3.3 Discovery path (alternative: paste advert URL)

1. Operator pastes a URL into the same Leads paste flow.
2. API calls `scrape_advert(url)` ([src/vacancysoft/intelligence/url_scrape.py:1](src/vacancysoft/intelligence/url_scrape.py)) → POSTs to the Playwright runner microservice (separate Azure Container App).
3. Runner returns `{title, company, location, description, postedDate, ...}`.
4. From here, identical to text-paste flow.

### 3.4 Enrichment

Triggered by `pipeline enrich` CLI or worker post-discovery.

`enrich_raw_jobs(session, limit, adapter_name)` in [src/vacancysoft/pipelines/enrichment_persistence.py:450](src/vacancysoft/pipelines/enrichment_persistence.py) iterates RawJobs that have no EnrichedJob yet. For each:

1. **Parse posted_at**: `parse_posted_date(raw_job.posted_at_raw)` — handles relative ("2 days ago"), ISO 8601, common formats.
2. **Normalise location**: `normalise_location(location_raw, employer)` → `{country, city, region}`. Loads rules from `configs/location_rules.yaml` (or hardcoded defaults). Longest-match-first to prevent "UK" beating "United Kingdom". Falls back to company-HQ lookup if location string is unparseable.
3. **Extract employer**: `_extract_employer_from_payload()` — pulls company name from listing_payload (Adzuna nests it as `company.display_name`; Reed as `employerName`; direct adapters use Source.employer_name).
4. **Extract seniority**: `_extract_seniority(title)` — maps title to one of {c_suite, head, managing_director, director, vp, senior, manager, mid, junior}.
5. **Three filter gates**:
   - **Geo**: `is_allowed_country(country)` — whitelist of {UK, USA, Canada, Australia, Germany, France, Netherlands, Belgium, Luxembourg, Ireland, Switzerland, Sweden, Singapore, Hong Kong, UAE, Japan}.
   - **Recruiter**: `is_recruiter(title, employer)` — title keywords ("recruiter", "talent acquisition") and a `configs/agency_exclusions.yaml` blocklist.
   - **Title relevance**: `is_relevant_title(title)` — checks against HIGH_RELEVANCE_PHRASES in [src/vacancysoft/classifiers/title_rules.py](src/vacancysoft/classifiers/title_rules.py).
6. **Stub on rejection**: failed gates create an `EnrichedJob` with `detail_fetch_status='geo_filtered'` (or recruiter/title) — the job is recorded but excluded from downstream stages. (Stubs accumulate; no automated cleanup.)
7. **Insert EnrichedJob** if all gates pass: `canonical_job_key = SHA1(title + city + country + source_id)` is the dedup key.

### 3.5 Classification

`classify_enriched_jobs(session, limit, adapter_name)` in [src/vacancysoft/pipelines/classification_persistence.py:1](src/vacancysoft/pipelines/classification_persistence.py).

Calls `classify_against_legacy_taxonomy(title)` ([src/vacancysoft/classifiers/taxonomy.py:35](src/vacancysoft/classifiers/taxonomy.py)) which returns:

```python
TaxonomyMatch(
    primary_taxonomy_key: str | None,   # "risk", "quant", "compliance", "audit", "cyber", "legal", "front_office"
    secondary_taxonomy_keys: list[str],
    sub_specialism: str | None,         # e.g. "Market Risk", "Credit Risk", "Internal Audit"
    sub_specialism_confidence: float,
    confidence: float,
)
```

`_TAXONOMY_RULES` is a per-category list of `(phrase, weight, sub_specialism)` tuples. Phrases checked longest-first.

Decision threshold lives in `decision_from_score(score: float)` at [src/vacancysoft/scoring/engine.py:48-54](src/vacancysoft/scoring/engine.py) and gates on the **composite export score** (not on `title_relevance` alone):

```python
def decision_from_score(score: float) -> str:
    t = get_thresholds()
    if score >= t.get("accepted", 0.75):
        return "accepted"
    if score >= t.get("review", 0.45):
        return "review"
    return "rejected"
```

The composite score is `compute_export_score()` from §3.6. Thresholds are configurable via `configs/scoring.toml`.

> **Known bug**: the composite `score` can clear 0.75 even when `primary_taxonomy_key is None`. The gate doesn't require a taxonomy match, so a high `title_relevance` plus decent location/freshness/completeness can carry a row over the line with a null taxonomy key. DB ends up with logically inconsistent rows. Exporters filter `WHERE primary_taxonomy_key IS NOT NULL`, so it doesn't reach customers — but the bug is real and should be fixed at source.

### 3.6 Scoring

`compute_export_score(...)` in [src/vacancysoft/scoring/engine.py:1](src/vacancysoft/scoring/engine.py). Six weighted inputs:

| Input | Weight | Source |
|---|---:|---|
| title_relevance | 0.30 | classifiers/title_rules.py |
| classification_confidence | 0.20 | TaxonomyMatch.confidence |
| location_confidence | 0.15 | location_normaliser success bits |
| freshness_confidence | 0.15 | age of job |
| source_reliability | 0.10 | SourceHealth |
| completeness | 0.10 | RawJob field-population ratio |

Weights live in `configs/scoring.toml`, fallback hardcoded. Thresholds also in TOML: ≥0.75 accepted, 0.45-0.75 review, <0.45 rejected.

Stored in `score_results` table linked 1:1 to `enriched_jobs`.

### 3.7 Dossier (LLM, 2 calls)

Triggered by operator clicking "Generate dossier" or the worker `process_lead` task.

`generate_dossier(enriched_job_id, session, force=False)` in [src/vacancysoft/intelligence/dossier.py:1](src/vacancysoft/intelligence/dossier.py).

**Call 1 — main analysis**:
- Model: `gpt-5.2` (default), or `deepseek-reasoner` if `use_deepseek_for_dossier=true`.
- Web search ON at `search_context_size="medium"` (lowered 2026-04-21 from "high" to save ~$0.017/lead).
- `reasoning_effort="low"` (gpt-5.2's base output already exceeds gpt-5-mini at "medium").
- Prompt: `resolve_dossier_prompt(category, job_data)` from [src/vacancysoft/prompts/intelligence/dossier/](src/vacancysoft/prompts/intelligence/dossier/).
- Output JSON: `{company_context, core_problem, stated_vs_actual, spec_risk, candidate_profiles, lead_score, lead_score_justification, hiring_manager_boolean}`.

**Call 2 — hiring-manager search**:
- Two routes (toggled by `use_serpapi_hm_search`, default true):
  - **SerpApi path**: SerpApi runs LinkedIn-targeted queries → snippets → gpt-4o-mini extracts named candidates. ~$0.046/lead, ~11s latency.
  - **OpenAI path** (fallback): `gpt-5.2` + Responses API + `web_search_preview` does both search and reasoning. ~$0.064/lead, ~56s latency.
- Always hard-wired to OpenAI for the extraction step (no DeepSeek toggle for HM lookups).
- Auto-fallback: SerpApi raises → OpenAI path runs, no dossier lost.

Persisted to `intelligence_dossiers` table. `call_breakdown` JSON has one entry per LLM call (variable schema — SerpApi rows have extra `serpapi_*` keys; cost-report tooling iterates and handles both).

`PROMPT_VERSION = "v1.3"` baked into each row. Caching: returns cached dossier if `core_problem` is non-empty (skip LLM unless `force=True`).

### 3.8 Campaign (LLM, 1 call)

`generate_campaign(dossier_id, session, force=False, user_context=None)` in [src/vacancysoft/intelligence/campaign.py:1](src/vacancysoft/intelligence/campaign.py).

- Model: `gpt-5.4` (default), or `deepseek-reasoner` if `use_deepseek_for_campaign=true`.
- `reasoning_effort="low"`, `temperature=0.4`, `max_tokens=16000`, `timeout=450s`, `response_format=json_object`.
- Fallback: if reasoner returns empty `emails` (token-burn on internal CoT), retries with `campaign_fallback_model = "gpt-4o"` (or `deepseek-chat` for DeepSeek).

**Voice layer** (the personalisation step):
- `build_user_context(session, user)` ([src/vacancysoft/intelligence/voice.py:1](src/vacancysoft/intelligence/voice.py)) merges:
  - `UserCampaignPrompt` rows (per-tone authored guidance, max 6 per user)
  - `VoiceTrainingSample` rows (operator-authored bootstrap samples)
  - Recent `SentMessage` rows where `status='sent'` (real outreach, capped at `VOICE_SAMPLE_WINDOW=10` per sequence)
- Resolver injects `{voice_layer}` into the campaign template; renders empty string for cold-start operators (output byte-identical to pre-voice).
- **Worker pre-gen passes `user_context=None`** — only the Builder UI's regenerate button passes it. This keeps queue-driven dossiers reproducible.

**Two prompt template versions** (hot-swap via `campaign_template_version` in app.toml):
- **v2** (default, 2026-04-20+): "Tone determines content source" — six distinct five-email arcs, each anchored on one dossier section.
  - formal → company_context (legitimacy)
  - informal → candidate_profiles (fit, growth)
  - consultative → core_problem (collaborative)
  - direct → stated_vs_actual (gap-closing)
  - candidate_spec → spec_risk (mitigation)
  - technical → lead_score_justification (commercial detail)
- **v1** (legacy): "Same message, different voice" — kept as rollback target.

Output: `{emails: [{sequence: 1-5, tone, subject, body}, ...]}` (5 × 6 = 30 emails).

Persisted to `campaign_outputs`. Caching: returns cached row if `outreach_emails` non-empty.

### 3.9 Send

> **Status**: code-complete and tested in dry-run. Keybridge approval received 2026-04-26; live-send now blocked on Entra app registration + PR D (`/launch` and `/cancel` API endpoints, ~50 LOC) + PR E (Bicep).

Operator clicks "Launch Campaign" with one of six tones selected → `POST /api/campaigns/{id}/launch` (PR D, not yet wired).

`schedule_outreach_sequence(redis, session, campaign_output_id, sender_user_id, recipient_email, tone, emails, cadence_days)` in [src/vacancysoft/worker/outreach_tasks.py:1](src/vacancysoft/worker/outreach_tasks.py):

1. Validates `emails` is length-5 with `{subject, body}`.
2. Validates `cadence_days` length-5 starting with 0 (default `[0, 7, 14, 21, 28]`).
3. Inserts 5 `SentMessage` rows (`status='pending'`, `scheduled_for`, `arq_job_id`).
4. Enqueues 5 deferred ARQ jobs `send-{sent_message_id}`.
5. Returns the 5 sent_message_ids.

ARQ fires `send_outreach_email(ctx, sent_message_id)` at scheduled time:
1. Re-reads the row; exits early if `status != 'pending'` (idempotency).
2. Calls `GraphClient.send_mail(...)` ([src/vacancysoft/outreach/graph_client.py:1](src/vacancysoft/outreach/graph_client.py)) — two-step: `POST /users/{id}/sendMail` (returns 202 empty body), then `GET /sentitems?$filter=...` to recover `id` + `conversationId`.
3. Updates row to `status='sent'`, populates `sent_at`, `graph_message_id`, `conversation_id`.
4. Enqueues first reply poll at `now + poll_interval_minutes` (default 10 min).

### 3.10 Reply detection

ARQ fires `poll_replies_for_conversation(ctx, conversation_id, sender_user_id)` every 10 min:

1. Finds earliest `SentMessage` with this conversation_id.
2. Bails if conversation older than `poll_max_days` (default 90).
3. `GraphClient.list_replies(user_id, conversation_id, since)` → `GET /users/{id}/messages?$filter=conversationId eq '...'` (metadata only — Mail.ReadBasic scope).
4. Filters out self-replies by email match. (**This will break post-Entra** — sender_user_id becomes a GUID; see §13.3 for the migration path.)
5. If replies found:
   - Inserts `ReceivedReply` rows (deduped by `graph_message_id`).
   - Finds pending SentMessages in the same conversation, calls `redis.abort_job(arq_job_id)`, sets `status='cancelled_replied'`.
6. Else: re-enqueues self at `now + interval`.

### 3.11 Manual cancel

`cancel_pending_sequence_manual(session, redis, campaign_output_id)`:
- SELECTs all pending SentMessages → `abort_job` each → `status='cancelled_manual'`.
- Does NOT insert ReceivedReply.
- Idempotent (re-running produces no extra effect).

---

## 4. Scraping layer

### 4.1 Adapter contract

Defined in [src/vacancysoft/adapters/base.py:1](src/vacancysoft/adapters/base.py) (87 LOC).

```python
class SourceAdapter(ABC):
    @abstractmethod
    async def discover(
        self,
        source_config: dict,
        cursor: str | None = None,
        since: datetime | None = None,
        on_page_scraped: PageCallback | None = None,
    ) -> DiscoveryPage: ...
```

Returns `DiscoveryPage(list[DiscoveredJobRecord], next_cursor, AdapterDiagnostics)`.

`DiscoveredJobRecord` carries: `external_job_id, title_raw, location_raw, posted_at_raw, summary_raw, discovered_url, apply_url, listing_payload, completeness_score, extraction_confidence, provenance`.

`AdapterDiagnostics` is the operational telemetry channel: `{warnings, errors, counters, timings_ms, metadata}`. Persisted as `SourceRun.diagnostics_blob` for post-hoc analysis.

`AdapterCapabilities` declares what an adapter supports: `discovery, detail_fetch, healthcheck, pagination, incremental_sync, api, html, browser, site_rescue`. Used by ops tooling to skip incompatible operations.

### 4.2 Adapter inventory

35 adapters totalling 9,611 LOC in [src/vacancysoft/adapters/](src/vacancysoft/adapters/). Two tiers:

#### Tier-1: API-based (high volume, well-tested)

| Adapter | Method | Config shape | Notes |
|---|---|---|---|
| workday | API | `{endpoint_url, job_board_url, tenant, shard, site_path}` | `derive_workday_candidate_endpoints()` synthesises endpoint from tenant URL — reuse this in any Workday tooling. |
| greenhouse | API | `{slug, job_board_url}` | Pagination via API. |
| lever | API | `{slug, job_board_url}` | JSON-LD fallback. |
| ashby | GraphQL | `{slug, job_board_url}` | Async GraphQL; salary in compensation object. |
| workable | API | `{slug, job_board_url}` | Multi-location variants. |
| smartrecruiters | API+HTML | `{slug, job_board_url}` | HTML fallback if API stalls. |
| adzuna | API | `{job_board_url}` | **Aggregator** — employer in `company.display_name`. |
| reed | API | `{job_board_url}` | **Aggregator** — employer in `employerName`. |
| google_jobs | API | `{job_board_url}` | **Aggregator** — Google structured-data index. |
| coresignal | API | `{job_board_url}` | **Aggregator** — newer; UK+NY defaults set 2026-04-25. |
| taleo | API+browser | `{job_board_url}` | Hybrid; URL shape varies by tenant. |
| pinpoint | API | `{slug, job_board_url}` | Slug-based. |
| teamtailor | API | `{slug, job_board_url}` | Tenant in subdomain. |
| silkroad | API | `{job_board_url}` | Older ATS. |

#### Tier-2: Browser-based (Playwright, error-prone)

| Adapter | Notes |
|---|---|
| icims | Heavy JS; selector rot common; "VSR" asp.net variant has location in `data-*` attributes. |
| oracle_cloud | Slow rendering; location often in data attributes. |
| successfactors | Multi-step loading; some boards need Firefox (Cloudflare); scroll-driven pagination. |
| eightfold | Heavy AJAX; null-await crash bug fixed PR #56. |
| hibob | Zero-jobs success bug (selector rot); null-await fixed PR #56. |
| efinancialcareers | Niche financial board; click-driven pagination. |
| selectminds | Navigation race condition (deferred). |
| clearcompany | Minimal usage; limited testing. |
| avature | 8 known tenants; Firefox session-reuse via `_FirefoxFetcher` in `scripts/backfill_avature_locations.py`. |
| phenom | Emerging; moderate JS. |
| recruitee | API+browser hybrid. |
| jazzhr | Small SaaS; low volume. |
| personio | European HR; career portal variant. |
| bamboohr | DNS failures; fallback domain patterns. |
| infor | Slow JS; large pagination. |
| adp | Two URL patterns (workforcenow vs careers); complex nav. |
| salesforce_recruit | Lightning vs Classic; location often in description text only. |
| beamery | Limited test data. |

#### Special: generic_browser fallback

[src/vacancysoft/adapters/generic_browser.py](src/vacancysoft/adapters/generic_browser.py) — 1,165 LOC. **Catch-all for any URL that doesn't match a known platform**. ~70% of pre-2026-04-22 source-discovery failures traced to selector rot here.

Key constants (in this file, not config):
- `LOCATION_HINT_SELECTORS` (lines 81-96): ordered CSS selectors for location containers.
- `_LOCATION_LABEL_PREFIXES` (108-113): strips "Location: London" → "London".
- `_LOCATION_MAX_LEN = 120` (103): rejects job-description-as-location garbage.
- `_LOCATION_REJECT_EXACT` (98-102): rejects "location", "remote", "hybrid".
- `_CANDIDATE_LINK_SELECTORS` (41-70): 30+ patterns to identify job title links.
- `JOBISH_HREF_FRAGMENTS` (148-189): ~40 URL fragments suggesting a link is a job page.
- `NON_JOB_HREF_FRAGMENTS` (115-146): nav, privacy, cookies — reject.
- `NON_JOB_TITLE_PREFIXES` (191-331): pagination buttons, language switchers, "Apply Now".

**Capture mode** (operator runbook item): `PROSPERO_GENERIC_CAPTURE_DIR` env var dumps rendered HTML before scraping. Used to extend selectors against new failing boards. Turn it on, run a scrape, inspect the captures, add selectors, ship.

**Active gaps**:
1. **70% of jobs lack city** — generic_site selectors miss 10K+ jobs from Macquarie, BofA, Equifax, Point72. Tracked in `~/.claude/plans/handoff-location-quality-phase2.md` (Thread 2) and `~/.claude/plans/handoff-steps-4-5.md` (Bucket C strategy).
2. **No JSON-LD fallback** — sites with embedded JobPosting schema should be parsed but aren't.

### 4.3 Source detection

[src/vacancysoft/api/source_detector.py:1](src/vacancysoft/api/source_detector.py) (175 LOC).

```python
def detect_platform(url: str) -> dict | None:
    """Returns {adapter, source_type, ats_family, board_name, config} or None."""
```

`PLATFORM_PATTERNS` (lines 15-125): 25+ regex patterns checked in order (most specific first). Each pattern has `pattern`, `adapter`, and `extract` lambda for pulling config keys from match groups.

**Limitations**:
- Doesn't detect Avature (8+ tenants); audit script has its own patterns.
- Doesn't detect njoyn.
- Phase 2 plan in `~/.claude/plans/radiant-honking-music.md` proposes extending detection + iframe URL extraction for the 149 outstanding misclassifications surfaced by PR #73's full-DB audit.

### 4.4 Source registry & seeding

[src/vacancysoft/source_registry/config_seed_loader.py:1](src/vacancysoft/source_registry/config_seed_loader.py) (430 LOC).

`PLATFORM_REGISTRY` (lines 49-72) maps platform_key → adapter details:

```python
PLATFORM_REGISTRY = {
    "workday": {"adapter": "workday", "source_type": "ats_api", "ats_family": "workday", ...},
    "greenhouse": {...},
    "generic_browser": {"adapter": "generic_site", "source_type": "browser_site", ...},
}
```

`seed_sources_from_config()` is **create-only** — existing rows are never updated by re-running the seed (PR #77 made this the default to protect audit corrections + UI edits). To update, use `scripts/apply_source_corrections.py` or the Sources UI.

`detect_adapter_from_url()` (lines 40-46): a priority-ordered regex override list (`_URL_ADAPTER_OVERRIDES`) that forces a platform assignment. Example: `r"\.hibob\.com"` → hibob, regardless of which config list the URL appears in.

> **Sync issue**: `_URL_ADAPTER_OVERRIDES` and `PLATFORM_PATTERNS` (in source_detector.py) are not kept in sync. They drift. Future refactor candidate.

---

## 5. Pipeline layer

### 5.1 Enrichment

Already covered in §3.4. Source: [src/vacancysoft/pipelines/enrichment_persistence.py](src/vacancysoft/pipelines/enrichment_persistence.py) (619 LOC).

### 5.2 Location normalisation

[src/vacancysoft/enrichers/location_normaliser.py](src/vacancysoft/enrichers/location_normaliser.py) (849 LOC).

```python
def normalise_location(location_raw: str | None, employer: str | None = None) -> dict[str, str | None]:
    """Returns {country, city, region}."""
```

Algorithm:
1. Load rules: prefer `configs/location_rules.yaml`; fall back to hardcoded defaults (UK cities, US states, EU capitals).
2. Split on separators (`, ` / ` - ` / ` / ` / `|`) → candidate tokens.
3. Match longest tokens first (prevents "UK" matching before "United Kingdom").
4. If multiple cities match, prefer longest key.
5. If country can't be inferred, use `_country_from_company()` (HQ lookup, ~50 companies hardcoded).
6. `_COUNTRY_ONLY` map for ambiguous names.

**Active leakage issues** (audit 2026-04-24):
- Top 15 cities include "USA | Bermuda" (×103), "USA | Remote" (×98) — work-type strings, not locations. Adapter location_raw includes work-type info. Fix: audit per-adapter location extraction.
- ~12 entries with full street addresses. Fix: parse with address library or reject.
- `location_region` is read from the dict but never populated by any enricher.
- `location_type` is **hardcoded `None`** at [src/vacancysoft/pipelines/enrichment_persistence.py:543](src/vacancysoft/pipelines/enrichment_persistence.py).

Both gaps are tracked in `~/.claude/plans/handoff-location-quality-phase2.md`.

### 5.3 Classification

[src/vacancysoft/classifiers/taxonomy.py](src/vacancysoft/classifiers/taxonomy.py) (480 LOC), [title_rules.py](src/vacancysoft/classifiers/title_rules.py) (287 LOC), [employment_type.py](src/vacancysoft/classifiers/employment_type.py) (77 LOC).

7 core markets: **risk, quant, compliance, audit, cyber, legal, front_office**.

Rules in `_TAXONOMY_RULES` (taxonomy.py:35-400) are per-category lists of `(phrase, weight, sub_specialism)` tuples.

`_CATEGORY_DEFAULT_SUB_SPEC` provides the safety-net sub-spec when a rule matches without an explicit one.

`title_relevance(title)` returns 0.0-1.0:
```python
if _looks_like_non_job_title(title): return 0.0
if any(p in title.lower() for p in HIGH_RELEVANCE_PHRASES): score += 0.5
if any(p in title.lower() for p in MID_RELEVANCE_PHRASES):  score += 0.25
return min(score, 1.0)
```

> **Bug**: the composite `score` can clear 0.75 even when `taxonomy.classify_against_legacy_taxonomy()` returns `primary_taxonomy_key=None`. The accept gate is on `score`, not on the taxonomy match (see §3.5). DB has logically inconsistent rows. Workaround: exporters filter `WHERE primary_taxonomy_key IS NOT NULL`. Real fix: tighten the accept gate to require a non-null taxonomy match in addition to score.

### 5.4 Scoring

Already covered in §3.6. Source: [src/vacancysoft/scoring/engine.py](src/vacancysoft/scoring/engine.py).

### 5.5 Exporters

[src/vacancysoft/exporters/views.py](src/vacancysoft/exporters/views.py) (93 LOC) — view query builders.

Queries:
- `accepted_only_query()` — `ScoreResult.export_decision == "accepted"`.
- `accepted_plus_review_query()` — accepted | review.
- `grouped_by_taxonomy_query()` — by primary_taxonomy_key.
- `client_segment_query(segment_name, config)` — filter by `configs/exporters.toml` segments.

`_base_export_query()` joins `EnrichedJob → ClassificationResult → ScoreResult → RawJob → Source` and prefers `EnrichedJob.team` (extracted employer) over `Source.employer_name`.

Output formats: Excel ([excel_exporter.py](src/vacancysoft/exporters/excel_exporter.py)), JSON ([json_exporter.py](src/vacancysoft/exporters/json_exporter.py)).

---

## 6. Intelligence layer

~3,520 LOC of code + ~46k LOC of prompts in [src/vacancysoft/intelligence/](src/vacancysoft/intelligence/) and [src/vacancysoft/prompts/intelligence/](src/vacancysoft/prompts/intelligence/).

### 6.1 Provider routing

Two providers, runtime-toggled:

[src/vacancysoft/intelligence/providers.py](src/vacancysoft/intelligence/providers.py) (263 LOC) defines `call_llm(provider=..., ...)`:

- **`LLMProvider.OPENAI`** → delegates to `client.call_chat`.
- **`LLMProvider.DEEPSEEK`** → async OpenAI client with `base_url="https://api.deepseek.com"`.

Toggles in `[intelligence]` of `configs/app.toml`:
- `use_deepseek_for_dossier` (default false)
- `use_deepseek_for_campaign` (default false)
- `use_deepseek_for_advert_extract` (default false)
- HM search is **hard-wired to OpenAI** (no toggle — policy decision, named-individual lookups should not flip via config).

DeepSeek silent caveats:
- `web_search=True` is **silently dropped** with a debug log. Operators may not realise dossier company_context becomes training-data-only when they flip this.
- `reasoning_effort` is **silently ignored** — DeepSeek uses fixed internal budget.

Return shape from both providers is identical, allowing hot-swap without downstream changes.

### 6.2 Client wrapper

[src/vacancysoft/intelligence/client.py](src/vacancysoft/intelligence/client.py) (209 LOC) — `call_chat(...)`:
- 3-attempt retry with exponential backoff (2s, 5s, 15s) on `APITimeoutError` and `RateLimitError`.
- Reasoning-model detection (gpt-5*, o1*, o3*, o4*) → switches to `max_completion_tokens` + optional `reasoning_effort`.
- Web-search routing → Responses API + `web_search_preview` tool (vs Chat Completions for plain calls).
- Returns `{parsed, raw_content, model, tokens_*, finish_reason, latency_ms}`.

### 6.3 Dossier (already partly covered in §3.7)

[src/vacancysoft/intelligence/dossier.py](src/vacancysoft/intelligence/dossier.py) (462 LOC).

**8-section JSON schema** with strict word caps:

```json
{
  "company_context": "Max 200 words. What the company does, peers, macro impacts.",
  "core_problem": "Max 120 words. Real problem driving the hire.",
  "stated_vs_actual": [
    {"jd_asks_for": "Max 40w", "business_likely_needs": "Max 40w"}
  ],
  "spec_risk": [
    {"risk": "...", "severity": "high|medium|low", "explanation": "Max 60w"}
  ],
  "candidate_profiles": [
    {"label": "Profile A", "background": "Max 40w", "fit_reason": "Max 40w", "outcomes": "Max 40w"},
    {"label": "Profile B", ...}
  ],
  "lead_score": 1-5,
  "lead_score_justification": "Max 80 words",
  "hiring_manager_boolean": "LinkedIn search boolean string"
}
```

**Prompt structure**:
- `base_dossier.py` (152 LOC) — system + user template, hard word caps.
- `category_blocks.py` (~17,800 LOC) — per-category research_scope, market_context_guidance, search_boolean_guidance, hm_function_guidance, hm_search_queries (v1 hand-authored, v2 generic with `[function]` + `[location]` slots), outreach_angle (v1 only).

`PROMPT_VERSION = "v1.3"` (2026-04-21: search_context high → medium, spec_risk cap 2 → 4, stated_vs_actual 2 → up to 4). Bumped on schema change so old rows don't masquerade as new.

### 6.4 HM search dual-path

Already covered in §3.7. Code in [hm_search_serpapi.py](src/vacancysoft/intelligence/hm_search_serpapi.py) (default) + the OpenAI fallback path inline in dossier.py:327-372.

### 6.5 Campaign

Already covered in §3.8. Source: [src/vacancysoft/intelligence/campaign.py](src/vacancysoft/intelligence/campaign.py) (211 LOC).

`base_campaign.py` is ~28,700 LOC of prompt across both v1 and v2. The v2 template is the operational default.

`resolver.py` (294 LOC) handles prompt assembly, voice-layer injection, and curly-brace escaping (operator-authored text goes through `.format()`, so braces must be escaped to avoid collisions).

### 6.6 Voice

[src/vacancysoft/intelligence/voice.py](src/vacancysoft/intelligence/voice.py) (168 LOC). Already covered in §3.8.

**Cold-start behaviour**: `build_user_context()` always returns a populated dict, even with no prompts and no samples. Resolver checks for actual content and renders empty string when nothing is present → byte-identical to pre-voice-layer output.

**Merge order**: SentMessage rows naturally push VoiceTrainingSample rows out of the `VOICE_SAMPLE_WINDOW=10` cap as real sends accumulate. No cleanup needed.

### 6.7 Cost tracking

[cost_report.py](src/vacancysoft/intelligence/cost_report.py) (283 LOC) reads `intelligence_dossiers.call_breakdown` and `campaign_outputs` and aggregates by (provider, model). Provider inferred from `model_used` prefix.

`call_breakdown` is a list of dicts, one per LLM call. SerpApi rows have `serpapi_searches`, `serpapi_cost_usd`, `llm_cost_usd` keys; OpenAI rows don't. Tooling iterates over the list and handles variable schema.

[pricing.py](src/vacancysoft/intelligence/pricing.py) (81 LOC) holds a shared per-model price table for both OpenAI and DeepSeek. Longest-prefix match for versioned models.

### 6.8 Cost envelope (per lead, current config)

| Stage | Model | Cost |
|---|---|---:|
| Dossier main call | gpt-5.2 + low reasoning + medium web_search | $0.060-0.080 |
| HM search (SerpApi) | SerpApi 3 queries + gpt-4o-mini extraction | $0.046 |
| HM search (OpenAI fallback) | gpt-5.2 + medium reasoning + high web_search | $0.064 |
| Campaign | gpt-5.4 + low reasoning | $0.025-0.030 |
| **Total (default config)** | | **~$0.135** |

DeepSeek toggle is roughly cost-neutral — cheaper per token but the reasoner emits 1.5-3× completion tokens.

---

## 7. Outreach layer

> **Live status**: code-complete and tested in dry-run. Live-send pre-launch checklist in §13.4.

### 7.1 Microsoft Graph integration

[src/vacancysoft/outreach/graph_client.py](src/vacancysoft/outreach/graph_client.py) (442 LOC).

**OAuth flow**: application credentials (not delegated). `POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with `client_credentials` + scope `https://graph.microsoft.com/.default`. Bearer token cached per-instance with 60s refresh buffer (~55 min reuse).

**send_mail two-step**:
1. `POST /users/{sender_user_id}/sendMail` returns 202 Accepted with empty body.
2. Recovery fetch: `GET /users/{sender_user_id}/mailFolders/sentitems/messages?$filter=...` to recover `id` + `conversationId`.
3. Returns `{graph_message_id, conversation_id, user_id, to_address, subject, sent_at, dry_run}`.

The recovery fetch has a rare race window where the message hasn't appeared in sentitems yet; the function returns empty IDs but still marks the row sent. Acceptable today; revisit if the rate becomes material.

**list_replies(user_id, conversation_id, since)**: `GET /users/{id}/messages?$filter=conversationId eq '...'`. Metadata only — Mail.ReadBasic scope, no body or attachments.

**Logging**: one JSON line per call (timestamp, user_id, op, http_status, latency_ms, ids). Strict JSON for machine-readability per security brief §6.

**Error handling**:
- 429 / 5xx → exponential backoff, max 3 attempts, honors Retry-After.
- 401 → force-refresh token once, retry.
- Other 4xx → raise `GraphError` with status_code, graph_error_code, request-id.

### 7.2 Secret retrieval

[secret_client.py](src/vacancysoft/outreach/secret_client.py) (129 LOC). Three paths in priority order:
1. **Dry-run** — returns literal `"DRY_RUN_SECRET"`. Never reaches Graph.
2. **Key Vault (production)** — `KEY_VAULT_URI` set → `azure-identity.DefaultAzureCredential` + `azure-keyvault-secrets`. Container App's managed identity needs `get` permission. Default secret name: `prospero-graph-client-secret`.
3. **Env var (dev)** — falls back to `GRAPH_CLIENT_SECRET` env if no Key Vault.

Lazy imports of Azure SDKs.

### 7.3 DRY_RUN kill switch

[dry_run.py](src/vacancysoft/outreach/dry_run.py) (102 LOC). `OUTREACH_DRY_RUN` env var:
- Unset → True (safe default)
- `true|1|yes|y|on` → True
- `false|0|no|n|off` → False
- Anything else → True (fail-safe)

**Read on every call** — operators can flip without restart.

`canned_send_mail()` returns synthetic UUIDs (`dryrun-msg-*`, `dryrun-conv-*`).
`canned_list_replies()` always returns empty list.
**DB writes still happen in dry-run** — full lifecycle is exercisable with synthetic Graph IDs.

### 7.4 ARQ task surface

[outreach_tasks.py](src/vacancysoft/worker/outreach_tasks.py) (440 LOC). Three registered ARQ tasks plus a non-ARQ helper:

- **`send_outreach_email(ctx, sent_message_id)`** — see §3.9.
- **`poll_replies_for_conversation(ctx, conversation_id, sender_user_id)`** — see §3.10.
- **`schedule_outreach_sequence(redis, session, ...)`** — non-ARQ helper that creates 5 SentMessage rows + enqueues 5 deferred ARQ jobs in one transaction.
- **`cancel_pending_sequence_manual(session, redis, campaign_output_id)`** — non-ARQ; aborts pending jobs + marks rows `cancelled_manual`.

**Self re-enqueueing**: poll task re-enqueues itself with deterministic job ID `poll-{conversation_id}-{int(timestamp)}` to bound the per-conversation polling at one job at a time.

### 7.5 Defaults & hardcoded values

| Constant | Value | Where |
|---|---:|---|
| Token refresh buffer | 60s | graph_client.py |
| Retry max attempts | 3 | graph_client.py |
| Retry backoff base | 2s | graph_client.py |
| Default secret name | `prospero-graph-client-secret` | secret_client.py |
| Default cadence | `[0, 7, 14, 21, 28]` | configs/app.toml [outreach] default_cadence_days |
| Default poll interval | 10 min | configs/app.toml [outreach] poll_interval_minutes |
| Poll ceiling | 90 days | configs/app.toml [outreach] poll_max_days |
| Sequence count | 5 | hardcoded throughout — see §14 |

### 7.6 Test coverage

| File | Size | Covers |
|---|---:|---|
| `tests/test_outreach_graph_client.py` | ~13 KB | Token, send_mail, list_replies, retries |
| `tests/test_outreach_tasks.py` | ~20 KB | schedule, send, poll, cancellation |
| `tests/test_outreach_secret_client.py` | ~7.5 KB | Dry-run, Key Vault, env var, missing secret |
| `tests/test_outreach_dry_run.py` | ~4 KB | is_dry_run() rules, canned helpers |

All tests use in-memory SQLite + fake Redis + injectable fake GraphClient. No real Azure/Graph calls.

---

## 8. Frontend

[web/](web/) — Next.js 16 app. **All client-side SWR; no SSR.** Tailwind v4. React 19. Always-on Next.js dev mode in development (no production build tested locally yet).

### 8.1 Pages

| Page | File | LOC | Hooks | Primary flows |
|---|---|---:|---|---|
| Dashboard | [web/src/app/page.tsx](web/src/app/page.tsx) | 668 | `useSWR("/dashboard")`, `useCurrentUser()` | 5 stat cards, live feed (filterable), 90-day chart (click-to-filter), By-Category breakdown |
| Sources | [web/src/app/sources/page.tsx](web/src/app/sources/page.tsx) | ~793 | `useSWR("/sources")`, `useVoicePrompts()` | Source cards, 6-dim filters, scrape/diagnose/delete actions, Add Source modal, CoreSignal Add Company flow |
| Leads | [web/src/app/leads/page.tsx](web/src/app/leads/page.tsx) | ~675 | SWR for /leads | Advanced search & filters, paste-advert, queue-for-campaign |
| Campaigns | [web/src/app/campaigns/page.tsx](web/src/app/campaigns/page.tsx) | ~151 | Queue state, dossier triggers | Queued leads, launch campaigns (button wired but PR D endpoint missing) |
| Builder | [web/src/app/builder/page.tsx](web/src/app/builder/page.tsx) | ~477 | Campaign template editor | Edit per-tone voice; "Save as training sample" button |
| Settings/Voice | [web/src/app/settings/voice/page.tsx](web/src/app/settings/voice/page.tsx) | 303 | `useVoicePrompts()` | Per-user voice training samples |

### 8.2 Global chrome

- `web/src/app/layout.tsx` — root layout.
- `web/src/app/components/Sidebar.tsx` (129 LOC) — navigation; **hardcoded user badge "Antony B./Pro Plan"** (replace with Entra `/.auth/me` post-Easy-Auth).

### 8.3 Shared hooks

- `lib/swr.ts` — exports `API = "http://localhost:8000/api"` ⚠️ **hardcoded** — needs `process.env.NEXT_PUBLIC_API_URL ?? "/api"` for Container App same-origin.
- `lib/useCurrentUser.ts` (138 LOC) — `/api/users/me` + debounced PATCH preferences (1s window, optimistic updates).
- `lib/useVoicePrompts.ts` (163 LOC) — voice CRUD; six tones; debounced sync per tone.
- `lib/features.ts` — feature flags.

### 8.4 State management pattern

All pages use **client-side SWR** with module-cached fetcher. No Next.js SSR or RSC. Mutations call `mutate("/dashboard")` etc. to invalidate caches. The 30s server-side cache is the bottleneck on UI freshness — within 30s of a mutation, the next fetch may still be stale.

### 8.5 Frontend gotchas for production

| Hardcoded value | File | Fix |
|---|---|---|
| API base URL | `lib/swr.ts:8` | `process.env.NEXT_PUBLIC_API_URL ?? "/api"` |
| User badge | `Sidebar.tsx` | Fetch from Entra `/.auth/me` post-Easy-Auth |
| CORS expectation | server.py | `allow_origins=["*"]` → env-driven origin list |

No pagination on dashboard recent-leads or sources list — current scale (~1500 sources) renders fine; revisit at 5k+.

---

## 9. API surface

### 9.1 Server entry

[src/vacancysoft/api/server.py](src/vacancysoft/api/server.py) (117 LOC) — FastAPI on `:8000`. CORS `allow_origins=["*"]` (hardcoded; env override planned for prod).

Startup sequence:
1. `_warm_caches()` — primes ledger + dashboard caches.
2. Redis pool connect (fallback to in-process for dev with no Redis).
3. Self-heal sweep — resets stuck queue items left in `processing` state from a previous unclean shutdown.

### 9.2 Routers

| Router | File | LOC | Endpoints |
|---|---|---:|---|
| leads | [routes/leads.py](src/vacancysoft/api/routes/leads.py) | 1203 | `/api/stats`, `/api/dashboard`, `/api/countries`, `/api/queue` (POST/GET), `/api/queue/{id}/send`, `/api/queue/{id}` (DELETE), `/api/leads/paste`, `/api/leads/{id}` (DELETE), `/api/leads/{id}/flag-location` |
| sources | [routes/sources.py](src/vacancysoft/api/routes/sources.py) | 491 | `/api/sources`, `/api/sources/{id}/jobs`, `/api/sources/detect`, `/api/sources` (POST), `/api/sources/{id}/scrape`, `/api/sources/{id}/diagnose`, `/api/sources/{id}` (DELETE) |
| add_company | [routes/add_company.py](src/vacancysoft/api/routes/add_company.py) | 928 | CoreSignal reverse-sourcing (search/preview/confirm phases) |
| campaigns | [routes/campaigns.py](src/vacancysoft/api/routes/campaigns.py) | 316 | `/api/agency` (POST), `/api/leads/{id}/dossier` (POST/GET), `/api/leads/{id}/campaign` (POST). **MISSING: `/launch`, `/cancel`** — PR D adds them; final paths TBD. |
| users | [routes/users.py](src/vacancysoft/api/routes/users.py) | 107 | `/api/users/me`, `/api/users/me/preferences` (PATCH), `/api/users` (admin), `/api/users` (POST admin) |
| voice | [routes/voice.py](src/vacancysoft/api/routes/voice.py) | 203 | `/api/voice/prompts` (GET/POST/DELETE) |

### 9.3 In-process caches (the multi-replica gotcha)

Three module-level dicts. `_sources_cache` and `_ledger_cache` live in [api/ledger.py:41-42](src/vacancysoft/api/ledger.py); `_dashboard_cache` lives in [routes/leads.py:55](src/vacancysoft/api/routes/leads.py). `routes/sources.py` only imports the ledger caches.

| Cache | Defined in | TTL | Key | Invalidated by |
|---|---|---:|---|---|
| `_dashboard_cache` | `routes/leads.py` | 30s | `__all__` | `clear_dashboard_cache()` on any mutation |
| `_sources_cache` | `api/ledger.py` | 30s | per country | `clear_ledger_caches()` |
| `_ledger_cache` | `api/ledger.py` | 30s | per country | `clear_ledger_caches()` |

**Multi-replica implication**: when scaling to >1 API replica, mutations on replica A invalidate only A's cache. Replica B can serve stale data for up to 30s. Currently not a problem (single-replica dev). For Container Apps multi-replica, migrate to Redis-backed cache.

### 9.4 The ledger function

`_build_source_card_ledger(country)` is the heart of the Sources page. Joins `Sources → RawJobs → EnrichedJobs → ClassificationResult → ScoreResult` and applies in order:

1. **Dedup by `canonical_url`** — global dedup across sources.
2. **Aggregator-cover** — direct source wins over Adzuna/Reed/CoreSignal/eFC/Google Jobs (same job from a direct adapter trumps the same job from an aggregator).
3. **Category counts** — per-source counts in each of the 7 core markets.
4. **Lead threshold** — `export_eligibility_score >= 6.0` (note: not the same threshold as `export_decision == 'accepted'` which uses score >= 0.75 in the [0,1] domain — there's a separate "lead-quality" gate that uses raw scores).
5. **last_run_status** — most recent `SourceRun.diagnostics_blob` summary.

### 9.5 Authentication

[src/vacancysoft/api/auth.py](src/vacancysoft/api/auth.py) — `get_current_user()`:
1. `X-Prospero-User-Email` header → lookup by email (case-insensitive).
2. Else exactly 1 active user → return it (single-user dev mode).
3. Else → 401.

Optional `PROSPERO_ADMIN_TOKEN` env var gates admin endpoints (POST /api/users, GET /api/users).

Last-seen tracking debounced to 1 write/user/min.

**Planned (with the Container Apps deploy)**: header swap to `X-MS-CLIENT-PRINCIPAL-NAME` (Azure Easy Auth default). Lookup logic stays identical. See §13.3 for the migration risk.

---

## 10. Database schema

19 tables. PostgreSQL (or SQLite for tests). SQLAlchemy ORM with declarative models in [src/vacancysoft/db/models.py](src/vacancysoft/db/models.py).

### 10.1 Tables

| Table | Purpose | Key columns |
|---|---|---|
| `sources` | Job-board source registry | `source_key` (unique), `employer_name`, `adapter_name`, `ats_family`, `active`, `fingerprint` (unique), `canonical_company_key`, `capability_blob` (JSON), `config_blob` (JSON), `seed_type` |
| `source_runs` | Per-scrape diagnostics | `source_id`, `started_at`, `finished_at`, `status`, `diagnostics_blob` (JSON) |
| `raw_jobs` | Scraped listings | `source_id`, `external_job_id`, `title_raw`, `location_raw`, `posted_at_raw`, `summary_raw`, `discovered_url`, `apply_url`, `listing_payload` (JSON), `detail_payload` (JSON), `content_hash` (global dedup) |
| `enriched_jobs` | Normalised | `raw_job_id` (unique), `canonical_job_key`, `location_country`, `location_city`, `location_region`, `location_type`, `location_text`, `posted_at`, `freshness_bucket`, `team` (extracted employer), `employment_type`, `seniority_level`, `detail_fetch_status` |
| `classification_results` | Taxonomy match | `enriched_job_id`, `primary_taxonomy_key`, `sub_specialism`, `classifier_version`, `decision`, `confidence`, `matched_terms` (JSON) |
| `score_results` | Composite score | `enriched_job_id` (unique), 6 component scores, `export_eligibility_score`, `export_decision`, `scoring_version` |
| `source_health` | Per-source reliability | `source_id`, `reliability_score`, `consecutive_failures`, `anomaly_flags` |
| `export_records` | Audit log | per-export row |
| `intelligence_dossiers` | LLM dossier | `enriched_job_id`, `prompt_version`, `category_used`, `model_used`, 8 dossier fields, `tokens_*`, `cost_usd`, `call_breakdown` (JSON list), `latency_ms` |
| `campaign_outputs` | LLM campaign | `dossier_id`, `model_used`, `outreach_emails` (JSON 5×6), `tokens_*`, `cost_usd`, `latency_ms` |
| `sent_messages` | Graph send queue | `campaign_output_id`, `sender_user_id`, `recipient_email`, `sequence_index` (1-5), `tone`, `scheduled_for`, `sent_at`, `graph_message_id`, `conversation_id` (indexed), `status`, `error_message`, `arq_job_id`, `subject`, `body` |
| `received_replies` | Inbound | `conversation_id` (indexed), `sender_user_id`, `graph_message_id` (unique), `from_email`, `received_at`, `subject`, `matched_sent_message_id` |
| `review_queue_items` | Campaign queue | per-lead state machine |
| `users` | User accounts | `id` (UUID), `entra_object_id` (nullable), `email` (unique), `preferences` (JSON, shallow merge) |
| `location_review_queue` | Operator-flagged locations | per-fix queue |
| `extraction_attempts` | Adapter diagnostics | per-attempt log |
| `user_campaign_prompts` | Per-tone overrides | `user_id`, `tone`, `instructions_text`; UniqueConstraint(user_id, tone) → max 6 rows/user |
| `voice_training_samples` | Operator-authored bootstraps | `user_id`, `sequence_index`, `tone`, `subject`, `body`, `source_enriched_job_id` |
| `agency_exclusions` | Recruiter blocklist (or YAML-driven) | `name` |

### 10.2 Foreign-key chain (the spine)

```
Source ──┬─ SourceRun (1:many)
         └─ RawJob (1:many)
              └─ EnrichedJob (1:1, unique FK)
                   ├─ ClassificationResult (1:many; latest used)
                   ├─ ScoreResult (1:1, unique)
                   └─ IntelligenceDossier (1:many; latest used)
                        └─ CampaignOutput (1:many — variants)
                             └─ SentMessage (1:many — 5 sequences × 6 tones)
                                  └─ ReceivedReply (FK nullable, via conversation_id)
```

### 10.3 Soft-delete policy

`Source.active = False` is the default deletion. Hard-delete is rare and only used for genuinely-bad-data corrections via `apply_source_corrections.py`.

`raw_jobs.deleted_at_source` is a soft flag set when an operator dismisses a lead via the UI. Aggregator-cover dedup respects this.

### 10.4 Migrations

[alembic/versions/](alembic/versions/) — 12 sequential migrations. Most recent: `0012_add_voice_training_samples`. Initial schema in 0001 (~11 tables); intelligence + outreach added incrementally (0003, 0008). Sector tagging migrations 0013 + 0014 were merged then reverted in PR #86.

Migrate forward: `alembic upgrade head`. Migrate down: `alembic downgrade -1`. Always back up Postgres before downgrade beyond one revision.

---

## 11. Configuration

### 11.1 `configs/app.toml`

The single behaviour-toggle file. Loaded by [src/vacancysoft/settings.py](src/vacancysoft/settings.py).

```toml
[app]
env = "dev"  # or "prod"
log_level = "INFO"
database_url = "postgresql://..."

[discovery]
concurrency = 8
allow_browser_fallback = true
raw_payload_dir = "./.data/raw"

[enrichment]
detail_fetch_concurrency = 4
max_retries = 3

[worker]
redis_url = "redis://localhost:6379/0"
max_concurrent = 25
job_timeout = 900  # seconds

[intelligence]
# Model routing (2026-04-20 quality upgrade)
dossier_model = "gpt-5.2"
campaign_model = "gpt-5.4"
campaign_fallback_model = "gpt-4o"
hm_search_model = "gpt-5.2"
hm_search_reasoning_effort = "medium"

# Provider toggles
use_deepseek_for_dossier = false
use_deepseek_for_campaign = false
use_deepseek_for_advert_extract = false
use_serpapi_hm_search = true  # default since 2026-04

# Web-search tuning
dossier_search_context_size = "medium"  # was "high", reduced 2026-04-21
hm_search_context_size = "high"

# Template versions (hot-swap rollback targets)
campaign_template_version = "v2"  # or "v1"
hm_template_version = "v2"

# Reasoning effort
dossier_reasoning_effort = "low"  # gpt-5.2 base output already exceeds gpt-5-mini at "medium"
campaign_reasoning_effort = "low"

# DeepSeek models (when toggles on)
dossier_model_deepseek = "deepseek-reasoner"
campaign_model_deepseek = "deepseek-reasoner"
campaign_fallback_model_deepseek = "deepseek-chat"

# SerpApi
hm_serpapi_max_searches = 3
hm_extraction_model = "gpt-4o-mini"

# Advert extraction
advert_extract_model = "gpt-4o-mini"
advert_extract_model_deepseek = "deepseek-chat"

# Globals
temperature = 0.4
max_tokens = 16000
timeout_seconds = 450

[outreach]
poll_interval_minutes = 10
poll_max_days = 90
default_cadence_days = [0, 7, 14, 21, 28]

[exports]
default_profile = "accepted_only"
```

### 11.2 Other config files

| File | Purpose |
|---|---|
| `configs/config.py` | Curated board lists for `db seed-config-boards` (create-only post-PR #77). |
| `configs/scoring.toml` | Score component weights (defaults match §3.6 table). |
| `configs/exporters.toml` | Export profile definitions + client segments. |
| `configs/agency_exclusions.yaml` | Recruiter-firm blocklist (used by `is_recruiter()`). 13 entries today. |
| `configs/location_rules.yaml` | City/country rules for `location_normaliser`. |
| `configs/legacy_routing.yaml` | Taxonomy + sub-specialisms (15 categories, 140+ sub-specs). |
| `configs/seeds/employers.yaml` | Seed list for `db seed-sources`. |

### 11.3 Environment variables

```bash
# Database
DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://localhost:6379/0

# OpenAI / DeepSeek / SerpApi
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com  # optional
SERPAPI_KEY=...

# Outreach
OUTREACH_DRY_RUN=true              # KILL SWITCH — keep true until launch day
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...             # dev only
KEY_VAULT_URI=https://...           # prod only
GRAPH_CLIENT_SECRET_NAME=prospero-graph-client-secret  # optional override

# Auth
PROSPERO_ADMIN_TOKEN=...            # optional, gates admin endpoints

# Operator
PROSPERO_GENERIC_CAPTURE_DIR=...    # diagnostic; dumps generic_site HTML
```

---

## 12. Worker / queue

[src/vacancysoft/worker/](src/vacancysoft/worker/) — ARQ + Redis.

### 12.1 Worker entrypoint

`arq src.vacancysoft.worker.main.WorkerSettings` — runs `max_concurrent=25` slots, job_timeout 900s.

### 12.2 Registered tasks

| Task | File | Purpose |
|---|---|---|
| `discover_source` | tasks.py | Run an adapter for one source; persist RawJobs + SourceRun. |
| `process_lead` | tasks.py | For an EnrichedJob: generate dossier → generate campaign → mark queue item ready. |
| `send_outreach_email` | outreach_tasks.py | Send one SentMessage; enqueue first reply poll. |
| `poll_replies_for_conversation` | outreach_tasks.py | Check replies; cancel pending; re-enqueue self. |

### 12.3 Idempotency

Every task re-reads its anchor row before acting and exits early if the row's status indicates the work is already done (or has been cancelled). This makes ARQ retries safe — the task can fire twice without double-sending.

### 12.4 Self re-enqueueing

`poll_replies_for_conversation` re-enqueues itself with deterministic job ID `poll-{conversation_id}-{int(timestamp)}`. This caps per-conversation polling at one pending job at a time and prevents fan-out.

### 12.5 Redis fallback

If `REDIS_URL` isn't reachable on startup, the API falls back to in-process queueing. This is dev-only — production must have Redis.

---

## 13. Operations & deployment

### 13.1 Local dev

`./start.sh` (macOS) — kills :8000, ensures redis + postgres are running (brew services), launches uvicorn API, ARQ worker, npm web dev. All on localhost.

`./run.sh` and `./launch_grok.py` are alternative launchers. **Do not consolidate** — see `~/.claude/projects/.../memory/feedback_keep_three_launchers.md`. Each serves a distinct purpose.

CLI entrypoint: `prospero` (alias for `python -m src.vacancysoft.cli.app`).

```bash
prospero db init
prospero db seed-sources                 # bootstrap from configs/seeds/employers.yaml
prospero db seed-config-boards            # create-only
prospero pipeline run                     # discover → enrich → classify → score → export
prospero pipeline run --adapter workday --source-key acme --unscraped --limit 10
prospero pipeline discover|enrich|classify|score
prospero export segment <name>
prospero agency add|remove|list
prospero user add|link-entra|list
prospero voice set-prompt|get-prompt
prospero db add-source <url>
prospero db cleanup-classifications        # one-off cleanup
prospero db reset-pipeline                 # nuke pipeline state, keep sources
```

### 13.2 Azure Container Apps deployment plan

Approved plan at [docs/deployment_plan.md](docs/deployment_plan.md) (311 lines). Summary:

**Phase 1 — code parameterisation (~6 lines of code change)**:
1. `web/src/app/lib/swr.ts` — env-driven API base.
2. `src/vacancysoft/settings.py` — env-driven `database_url`.
3. `src/vacancysoft/api/server.py` — env-driven CORS origin list.

**Phase 2 — Dockerfiles (4 images)**:
- `api` (FastAPI + uvicorn).
- `migrate` (alembic, run-once on each release).
- `worker` (ARQ + **Playwright Chromium pre-installed**, ~400MB image).
- `web` (Next.js 16 standalone build).

**Phase 3 — Bicep IaC**:
- Resource Group, Log Analytics, Application Insights.
- Azure Container Registry.
- Key Vault (graph secret, OpenAI key, DeepSeek key, SerpApi key).
- Postgres Flexible Server (B1ms, ~£10/mo).
- Redis (C0 Basic, ~£12/mo).
- Container Apps Environment.
- 3 Container Apps: api (1-3 replicas), web (1-2), worker (1-N + KEDA scaling).
- Cron job for scheduled scrapes.
- Azure Files mount for `/artifacts/raw/` (currently ephemeral in containers).

**Phase 4 — GitHub Actions**:
- Build/push 4 images to ACR.
- Run migrate.
- Parallel update of api + web + worker.
- Smoke test behind Entra Easy Auth.

**Single-domain reverse proxy**: `prospero.<corp>.com` with path routing — `/api/*` → api Container App, `/*` → web Container App.

### 13.3 Auth transition (the self-reply gotcha)

Today: `X-Prospero-User-Email` header → lookup user by email.

Post-Easy-Auth: Entra injects `X-MS-CLIENT-PRINCIPAL-NAME` (the user's UPN) into requests at the ingress. Lookup logic stays identical (email-shaped string, lookup by email). **Code change ≈ 1 line in `auth.py`.**

But: `outreach_tasks.poll_replies_for_conversation` filters self-replies by **email match between `from_email` and `sender_user_id`** ([src/vacancysoft/worker/outreach_tasks.py:314-317](src/vacancysoft/worker/outreach_tasks.py)). Once `sender_user_id` becomes the Entra GUID (the user's `id` field, not their email), the email-match logic breaks. Fix: store `sender_email` separately on `SentMessage` rows, or look up the user by GUID at poll-time and compare against their email.

This is small (~10 LOC) but it's a silent landmine — the email match would just stop matching and self-replies would start flowing through as real replies, cancelling pending sequences. Test before flipping DRY_RUN=false.

### 13.4 Pre-launch checklist (live-send)

**Critical blockers** (must all be done):
- [x] Keybridge security approval. ✅ Received 2026-04-26.
- [ ] Entra app registration with `Mail.Send` + `Mail.ReadBasic` application permissions.
- [ ] Entra admin consent.
- [ ] Application Access Policy scoped to `prospero-users` group.
- [ ] PR D — `/api/campaigns/{id}/launch` and `/cancel` endpoints (~50 LOC).
- [ ] PR D — Builder UI launch button + sequence-status view.
- [ ] PR E — Bicep Key Vault + Container App env-var bindings.
- [ ] Smoke test (real send + reply + cancellation) in production with `OUTREACH_DRY_RUN=true` first.
- [ ] Self-reply filter refactored for Entra GUIDs (§13.3).
- [ ] All four env vars set in production: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `KEY_VAULT_URI`, `GRAPH_CLIENT_SECRET_NAME`.
- [ ] `OUTREACH_DRY_RUN=false` on launch day.

**High-priority (do before scale-up)**:
- [ ] Ops runbook.
- [ ] Application Insights alerts (send failure rate, reply detection latency, queue depth).
- [ ] Legal review of email footer (GDPR, unsubscribe link, sender identity).
- [ ] Move API caches from in-process to Redis if running >1 API replica.

**Nice-to-have (post-launch)**:
- [ ] Settings page stats (per-user send/reply counts).
- [ ] Voice-tuning feedback loop (improve guidance from reply rates).
- [ ] Cost-report dashboard widget.
- [ ] Campaign manager page (track active sequences, manual cancel, audit).

### 13.5 DRY_RUN as the production safety net

`OUTREACH_DRY_RUN=true` is the **only** thing standing between a misconfigured prod environment and live mail. It's a runtime check (read on every Graph call), and the env var read is fail-safe (anything other than explicit "false" stays true).

In production, this means:
1. Set `OUTREACH_DRY_RUN=true` on initial Container App deploy.
2. Verify all integrations work end-to-end (DB writes happen in dry-run, only Graph calls are stubbed).
3. Flip to `OUTREACH_DRY_RUN=false` after smoke test.
4. Keep Container App restart-cheap so flipping back to `true` is instant if needed.

There is **no immutability safeguard** — anyone with Container App config rights can flip the value. Consider locking it down via Bicep + RBAC if compliance requires.

---

## 14. What's not yet built — gap register

### 14.1 Pre-launch (live-send blocked)

- **PR D** — `/api/campaigns/{id}/launch` + `/cancel` endpoints. ~50 LOC. Code on the worker side (`schedule_outreach_sequence`) is complete; just needs the route layer.
- **PR D** — Builder UI launch button + sequence-status view.
- **PR E** — Bicep IaC module + ops runbook.
- **Entra app registration** — external dependency, not code. (Keybridge approval received 2026-04-26.)

### 14.2 Active improvement threads

| Thread | Plan | Status |
|---|---|---|
| Location quality Phase 2 (4 sub-threads) | `~/.claude/plans/handoff-location-quality-phase2.md` | Active. region/type wiring, city coverage, country→city leakage, raw-address parsing. |
| Adapter failures Phase 6 | `~/.claude/plans/fix-adapter-failures.md` | Phase 1, 3, 4a merged; Phase 4b (SuccessFactors actual fix) and Phase 6 (long-tail per-board) pending. |
| Bucket C selectors | `~/.claude/plans/handoff-steps-4-5.md` | Complementary to Phase 2 Thread 2. |
| Phase 2 audit upgrade (iframe URL extraction) | `~/.claude/plans/radiant-honking-music.md` | Designed; would unblock 149 ATS misclassifications surfaced by PR #73. |
| vacancysoft → prospero rename | `~/.claude/plans/rename-vacancysoft-to-prospero.md` | Designed; ~20 file change. |
| DB-as-source-of-truth (PRs 2-3) | `~/.claude/plans/db-as-source-of-truth.md` | PR 1 (create-only seed) shipped as PR #77. PRs 2-3 (export tooling + bootstrap) queued. |

### 14.3 Parked / not started

- **Multi-tenancy** — no `tenant_id`/`org_id` columns. All data visible to all users in single-tenant. Full SaaS migration is 12+ months out.
- **JSON-LD fallback in generic_site** — sites with embedded JobPosting schema should be parsed structurally; not yet wired.
- **Detail backfill in main pipeline** — [detail_backfill.py](src/vacancysoft/pipelines/detail_backfill.py) (380 LOC: Workday + generic-HTML + SmartRecruiters detail fetchers) is wired into the CLI as `db backfill-detail` ([cli/app.py:47](src/vacancysoft/cli/app.py) imports it; called at lines 647, 1298, 1331, 1514, 1556) but is **not auto-run by `pipeline run`**. Could fold into the standard pipeline if detail-page enrichment becomes a default step.
- **Multi-source concurrency** — pipeline currently serial per-source.
- **A/B testing harness** — no built-in experiment harness for template versions, models, prompts.
- **Streaming responses** — all LLM calls are request-response.
- **Dossier chat-back** — no follow-up Q&A after dossier returns.
- **Voice-tuning feedback loop** — no automated improvement of tone guidance from reply rates.
- **Cost dashboard widget** — `cost_report.py` is CLI-only.
- **Campaign sequence count is hardcoded 5** — `SentMessage.sequence_index` is 1-5; no variable-length support today.
- **Hardcoded six tones** — formal/informal/consultative/direct/candidate_spec/technical baked into schema, prompts, and UI.
- **Paste-dedupe is exact-URL-only** — fuzzy duplicate detection (paste ATS URL when aggregator already has it) tracked in memory `paste_dedupe_fuzzy.md`.

### 14.4 Known bugs (workarounds in place)

- **Null-taxonomy accepted** (§5.3) — DB has logically inconsistent rows. Workaround: exporter filter. Real fix: tighten accept gate.
- **Graph sendMail recovery race** (§7.1) — rare, sets empty IDs, row still marked sent. Acceptable today.
- **Self-reply filter post-Entra** (§13.3) — will silently break when sender becomes GUID. Fix before launch.
- **Filter stubs accumulate** (§3.4) — no retention policy. DB grows unbounded. Add archival or delete-after-90d.
- **Location flags accumulate** — same job can be flagged 5 times. Review UI must dedup.
- **Preferences shallow merge gotcha** — nested updates require sending whole top-level key. Documented in [routes/users.py](src/vacancysoft/api/routes/users.py).
- **In-process API cache + multi-replica** — see §9.3.

### 14.5 Documentation gaps

- **No central "adapter MANIFEST"** — 35 files with no tier/capability matrix. Suggestion: `src/vacancysoft/adapters/MANIFEST.md`.
- **PLATFORM_PATTERNS and \_URL_ADAPTER_OVERRIDES drift** — should be a single source of truth (§4.4).
- **Capture mode is a hidden diagnostic** — `PROSPERO_GENERIC_CAPTURE_DIR` undocumented outside code. Add to operator runbook.
- **No operator-facing cost dashboard** — `cost_report.py` is CLI-only.
- **No dossier versioning strategy** — no auto-migration of old rows when prompt schema changes.
- **No voice-layer audit trail** — operator changes to UserCampaignPrompt aren't logged with reason or source.
- **No end-to-end smoke test** — `test_smoke.py` exists but doesn't exercise the full discover→enrich→classify→score→dossier→campaign chain.

---

## Appendix A: File-tree map (top 3 levels)

```
fuck_vacancysoft_refined/
├── web/                                 Next.js 16 frontend
│   └── src/app/{page.tsx,leads,sources,campaigns,builder,settings,components,lib}
├── src/vacancysoft/                     Python backend
│   ├── api/{server.py,ledger.py,source_detector.py,schemas.py,routes/}
│   ├── db/{models.py,engine.py,session.py,base.py}
│   ├── cli/app.py                       2,499 LOC unified CLI
│   ├── pipelines/                       discover→enrich→classify→score→export
│   ├── adapters/                        35 source-specific scrapers (9,611 LOC)
│   ├── classifiers/                     taxonomy.py, title_rules.py, employment_type.py
│   ├── scoring/                         engine.py + persistence
│   ├── exporters/                       views, excel, json, profiles
│   ├── enrichers/                       location_normaliser, date_parser, recruiter_filter
│   ├── intelligence/                    LLM dossier, campaign, voice, advert_extraction
│   ├── prompts/intelligence/            base_dossier, base_campaign, category_blocks, resolver
│   ├── outreach/                        graph_client, dry_run, secret_client
│   └── worker/                          ARQ tasks
├── alembic/versions/                    12 migrations
├── configs/{app.toml, config.py, scoring.toml, exporters.toml, *.yaml, seeds/, review/}
├── tests/                               30+ test files
├── docs/{deployment_plan, architecture, launch_plan, outreach_email, ...}
├── scripts/                             ad-hoc + maintenance scripts
└── pyproject.toml, start.sh, run.sh, launch_grok.py
```

### LOC summary

| Layer | LOC |
|---|---:|
| Adapters (35 + generic) | ~10,800 |
| Pipeline | ~3,200 |
| Intelligence (code) | ~3,520 |
| Prompts (data) | ~46,700 |
| Outreach + worker | ~1,113 |
| API surface | ~3,365 |
| CLI | 2,499 |
| Frontend | ~3,100 |
| Tests | ~6,989 |

---

## Appendix B: Glossary

| Term | Meaning |
|---|---|
| **Adapter** | A source-specific scraper that knows how to extract jobs from one ATS or job board (e.g. Workday adapter, Greenhouse adapter). Implements `SourceAdapter.discover()`. |
| **Aggregator** | A job board that re-publishes jobs from many companies (Adzuna, Reed, Google Jobs, CoreSignal, eFinancialCareers). Lower-priority data source than direct adapters. |
| **Aggregator-cover** | The dedup rule that says: if the same job is found via an aggregator AND a direct adapter, the direct version wins. |
| **ATS** | Applicant Tracking System. The platform a company uses to publish jobs (Workday, Greenhouse, Lever, etc.). |
| **Campaign** | A 5-sequence × 6-tone email matrix (30 emails) generated from a dossier, persisted to `campaign_outputs`. |
| **CoreSignal** | An aggregator we use to *reverse-source* — find companies hiring in our markets. The "Add Company" flow on the Sources page. |
| **Dossier** | The 8-section LLM-generated intelligence brief on one job opportunity, persisted to `intelligence_dossiers`. |
| **DRY_RUN** | The kill switch (`OUTREACH_DRY_RUN` env var) that stubs all Microsoft Graph calls. Default true. |
| **Easy Auth** | Azure App Service / Container Apps built-in authentication that injects identity headers at the ingress. We'll use it with Entra ID. |
| **Entra** | Microsoft's identity service (formerly Azure AD). |
| **Filter gate** | One of the three pre-classification rejection rules (geo, recruiter, title-relevance) applied during enrichment. |
| **generic_site** | The catch-all browser adapter ([adapters/generic_browser.py](src/vacancysoft/adapters/generic_browser.py)) that handles any URL not matching a known platform. 1,165 LOC of selector heuristics. |
| **HM** | Hiring Manager. The named individual we identify via the dossier's HM-search call. |
| **Ledger** | The Sources-page data structure built by `_build_source_card_ledger()` — joins, dedup, aggregator-cover, category counts. |
| **Lead** | An EnrichedJob that's accepted (or in review) — a job worth pursuing. |
| **PLATFORM_PATTERNS** | The regex list in `source_detector.py` that maps URL → adapter. |
| **PROMPT_VERSION** | A version string baked into each dossier row so old rows don't masquerade as new when the prompt schema changes. |
| **Sequence** | One of the five emails in a campaign sent at days 0/7/14/21/28. |
| **SourceRun** | One scrape attempt by one adapter for one source. Carries `diagnostics_blob`. |
| **Sub-specialism** | A finer-grained classification under a primary taxonomy key (e.g. "Market Risk" under "risk"). |
| **Tone** | One of the six campaign tones: formal, informal, consultative, direct, candidate_spec, technical. |
| **Voice layer** | The per-user personalisation injected into the campaign prompt: authored tone guidance + recent sent messages + bootstrap training samples. |

---

## Appendix C: Active plans index

All plans live under `~/.claude/plans/`. The ones currently load-bearing for ongoing work:

| Plan | Topic |
|---|---|
| `handoff-location-quality-phase2.md` | 4 location-quality threads (region/type wiring, city coverage, leakage, addresses). |
| `fix-adapter-failures.md` | Per-adapter failure recovery (Phase 1, 3, 4a merged; 4b + 6 pending). |
| `handoff-steps-4-5.md` | Bucket C selector strategy for generic_site. |
| `radiant-honking-music.md` | Phase 2 audit upgrade (iframe URL extraction; 149 ATS misclassifications). |
| `rename-vacancysoft-to-prospero.md` | Package rename. |
| `db-as-source-of-truth.md` | Seed-loader → DB migration (PR 1 shipped, 2-3 queued). |
| `handoff-steps-4-5.md` + `handoff-location-quality-phase2.md` | The two highest-priority active workstreams. |

Reverted plans (kept for reference, not active):
- Sector tagging — reverted in PR #86 after 82% of cards landed as 'unknown'. Plan at `~/.claude/plans/sector-tagging-plan.md` and post-mortem at `/tmp/sector_tagging_final_report.md`.

---

**End of document.**

If this is your first session on Prospero: read §1, §2, §3 in full. Skim §4-13. Bookmark §14 as the gap register. Use §11 + Appendix A as reference.
