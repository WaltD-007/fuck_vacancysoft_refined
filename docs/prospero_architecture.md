# Prospero — system architecture

> **Living document.** Update on any meaningful structural change. Last meaningful updates: 2026-04-27 (tracking subsystem + canary delta).

This is the top-level map of how Prospero fits together. Three companion docs are referenced throughout and stay authoritative for their narrower areas:

- **[outreach_email.md](./outreach_email.md)** — Microsoft Graph integration, dry-run mode, secret rotation
- **[deployment_plan.md](./deployment_plan.md)** — approved Azure topology
- **[azure_deployment_delta.md](./azure_deployment_delta.md)** — local-to-Azure migration checklist
- **[../.claude/plans/handoff-messaging-and-campaigns-phase1.md](../.claude/plans/handoff-messaging-and-campaigns-phase1.md)** — Barclay Simpson rollout build plan

If you change the data model, add a new long-running task, swap mail providers, or move infrastructure: update this doc.

---

## What Prospero is

A coverage-first job-scraping pipeline plus a recruitment-outreach engine layered on top. Primary use case: a recruitment firm's BDM team discovering hiring activity at financial-services firms, generating intelligence about each role + company, then running a multi-step outreach sequence at the hiring manager.

Two halves of the product:

1. **Discovery + intelligence** (the moat). Scrapes 1,500+ company career sites and aggregators, classifies and scores each role, generates a per-role dossier and a 30-email outreach campaign (5 sequence steps × 6 tone variants).
2. **Outreach engine** (table stakes). Sends the generated emails on behalf of a named operator via Microsoft Graph, polls for replies, auto-cancels remaining sends when a reply lands, tracks opens and clicks.

Both halves run in one repo, one DB, one API server. Deployed as separate Container Apps when on Azure.

---

## Technology surface

| Area | Choice |
|---|---|
| Backend language | Python 3.13 |
| Web framework | FastAPI (uvicorn) |
| Async background work | ARQ on Redis |
| Frontend | Next.js 16+ (client-only pages, SWR) |
| Database | PostgreSQL 17 |
| ORM | SQLAlchemy 2.x (typed `Mapped[...]`) |
| Migrations | Alembic |
| LLM | OpenAI gpt-5.2 (dossier), gpt-4o (some campaign paths) |
| Browser automation | Playwright (Chromium) |
| Mail | Microsoft Graph (`Mail.Send` + `Mail.ReadBasic`, Application permissions) |
| Cloud target | Azure Container Apps + Azure DB for PostgreSQL + Azure Cache for Redis + Key Vault |

---

## Top-level component map

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend  —  Next.js (web/)                                         │
│   /              dashboard                                           │
│   /sources       source registry + Add Company flow                  │
│   /leads         queue review + per-lead detail                      │
│   /builder       Campaign Builder (lead → 30 emails → Launch)        │
│   /campaigns     tracker view (mock until Phase 1)                   │
│   /settings/*    voice / scoring / integrations                      │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  /api/*
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  API  —  FastAPI (vacancysoft.api.server)                            │
│   routes/leads          queue, dashboard, paste, flag-location       │
│   routes/sources        directory + scrape + diagnose                │
│   routes/add_company    fuzzy-match → confirm → commit               │
│   routes/campaigns      dossier + campaign-gen + launch + cancel     │
│   routes/tracking       /t/o/<token>, /t/c/<token>  (anonymous)      │
│   routes/users          identity + per-user prefs                    │
│   routes/voice          per-tone prompts + training samples          │
│  auth.py — get_current_user (header today, Easy Auth post-Phase 1)   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                  ┌──────────┴───────────┐
                  ▼                      ▼
┌──────────────────────────┐   ┌─────────────────────────────────────┐
│  Worker  —  ARQ          │   │  Postgres                           │
│  (vacancysoft.worker)    │   │   raw_jobs / enriched_jobs /        │
│   tasks.process_lead     │   │   classification / score / dossier /│
│   tasks.scrape_source    │   │   campaign / sent_messages /        │
│   outreach_tasks.send_*  │   │   received_replies / open_events /  │
│   outreach_tasks.poll_*  │   │   click_events / users / voice ...  │
│   self_heal              │   └─────────────────────────────────────┘
│   25 concurrent · 900s   │
│   per-job timeout        │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Redis (ARQ queue + deferred sorted-set)                             │
│   - immediate jobs (process_lead, scrape_source, send_outreach_email)│
│   - deferred jobs  (send at +N days, poll at +10/30 min)             │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  External                                                            │
│   - OpenAI API (dossier + campaign generation)                       │
│   - Microsoft Graph API (sendMail, list messages)                    │
│   - Playwright Chromium (per-source scraping)                        │
│   - Optional: SerpApi, Adzuna, Reed, Coresignal, DeepSeek            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Discovery + intelligence pipeline

Lead path from raw scrape through to a fully-prepared campaign:

```
Source registry           ─►   raw_jobs              (~1500 sources, 100k jobs)
   │                              │
   ▼                              ▼
adapter (per-platform)     ─►  enriched_jobs         (parsed title/team/location/employment)
                                  │
                                  ▼
                              classification         (category + sub-specialism)
                                  │
                                  ▼
                              score_results          (lead_score 1-10)
                                  │
                                  ▼
                              review_queue_items     (status: pending → generating → ready)
                                  │
                                  ▼
                              intelligence_dossiers  (LLM: company context, hiring managers, candidate spec)
                                  │
                                  ▼
                              campaign_outputs       (LLM: 30 emails = 5 sequence × 6 tones)
```

Adapters live under [`src/vacancysoft/adapters/`](../src/vacancysoft/adapters/) — one per ATS family (Workday, Greenhouse, Lever, Ashby, etc.) plus a generic_site fallback. Each implements a small interface; the pipeline runner iterates registered sources and calls the right adapter per source.

LLM calls are strict-schema (Pydantic-validated outputs) and model-configurable per stage in `configs/app.toml`. Dossier and campaign generation cache outputs in Postgres (`intelligence_dossiers`, `campaign_outputs`) so re-opening a lead doesn't regenerate.

---

## Outreach pipeline

The launchable half. Stack from the user clicking Launch in the Builder through to opens and clicks landing back in the DB:

### Send path

```
Campaign Builder (web/src/app/builder/page.tsx)
   │  click "Launch Campaign"
   ▼
POST /api/campaigns/{campaign_output_id}/launch
   │  resolves operator identity, extracts 5 (subject, body) for tone,
   │  resolves recipient (override or dossier HM)
   ▼
schedule_outreach_sequence (worker/outreach_tasks.py)
   │  creates 5 SentMessage rows (status='pending')
   │  enqueues 5 deferred ARQ jobs at the right times
   ▼
ARQ deferred queue (Redis sorted set)
   │  fire at scheduled_for
   ▼
send_outreach_email (worker/outreach_tasks.py)
   │  re-reads the row, exits early if not 'pending' (idempotent)
   │  if tracking enabled: inject_pixel + rewrite_links → mutate row.body
   │  GraphClient.send_mail()
   │  on success: row.status='sent', graph_message_id, conversation_id
   │  enqueue first poll_replies_for_conversation at +interval
```

### Reply path

```
ARQ deferred queue
   │  fire poll_replies_for_conversation at +N min
   ▼
poll_replies_for_conversation (worker/outreach_tasks.py)
   │  Graph: list messages where conversationId = X since first_send_at
   │  filter out self-replies (resolve sender_user_id → users.email
   │     for accurate comparison; bug fixed 2026-04-27)
   │  no replies? re-enqueue self at +interval (bounded to 90 days)
   │  reply found?
   ▼
   ├─► insert ReceivedReply row
   ├─► ARQ abort_job for every still-pending sequence step
   ├─► flip those SentMessage rows to status='cancelled_replied'
   └─► (UI surfaces this on the Campaigns tracker)
```

### Tracking path

```
Recipient opens email
   │  mail client renders <img src="<tracking_domain>/t/o/<token>">
   ▼
GET /t/o/{token} (api/routes/tracking.py — anonymous)
   │  verify HMAC token → sent_message_id
   │  dedupe within 60s window
   │  insert open_events row + 200 image/gif (1×1 pixel)

Recipient clicks a link
   │  rewritten <a href="<tracking_domain>/t/c/<token>">
   ▼
GET /t/c/{token} (api/routes/tracking.py — anonymous)
   │  verify HMAC token → sent_message_id, original_url
   │  scanner heuristic: <120s OR known scanner UA → likely_scanner=true
   │  insert click_events row + 302 to original_url
```

### Manual cancel

```
Operator clicks Stop (post-Phase 1; today the cancel endpoint exists
but no UI button beyond the Builder)
   │
   ▼
POST /api/campaigns/{id}/cancel
   │
   ▼
cancel_pending_sequence_manual (worker/outreach_tasks.py)
   │  ARQ abort_job for every pending row
   │  flip SentMessage rows to status='cancelled_manual'
```

---

## Data model — outreach-relevant tables

Full model lives in [`src/vacancysoft/db/models.py`](../src/vacancysoft/db/models.py). What an engineer touching the outreach stack needs to know:

### `sent_messages`
One row per scheduled or completed send. Created in batches of 5 by `schedule_outreach_sequence`. The `status` column drives every UI surface and worker behaviour: `pending → sent | failed | cancelled_manual | cancelled_replied`. `body` stores the **as-sent** body (with tracking pixel injected and links rewritten) so debugging "why does this email show 5 opens" is straightforward — the stored body matches what arrived.

### `received_replies`
One row per Graph-observed inbound reply. Linked to `sent_messages` by `conversation_id` (Graph's thread identifier). `matched_sent_message_id` is the best-match earliest-pending row in the conversation at reply time — used for UI display, not enforced uniquely.

### `open_events` *(added 2026-04-27, migration 0013)*
One row per pixel-load. Deduped at write time within a 60-second window per `sent_message_id` (Outlook preview pane fires twice). `likely_apple_mpp` flag set when user-agent matches a known image-prefetch pattern. `ip_hash` is HMAC-SHA256 of the source IP keyed by a salt derived from `PROSPERO_TRACKING_SECRET` — deterministic-per-secret-era, no raw IPs stored.

### `click_events` *(added 2026-04-27, migration 0013)*
One row per link-click. NOT deduped — repeat clicks are real signal. `likely_scanner` flag set when either (a) `clicked_at - sent_at < 120s` or (b) user-agent matches a known mail-security scanner (Mimecast, Microsoft Safe Links, Proofpoint, etc.). Stored regardless so aggregations can choose to include or exclude.

### `campaign_outputs`
Cached LLM output per dossier. The `outreach_emails` JSON column holds the 30-email block in a `{"emails": [{"sequence": 1, "variants": {tone: {subject, body}}}, ...]}` shape. The launch endpoint indexes into this by tone to extract 5 `(subject, body)` pairs per send.

### `users`
Per-operator identity. `entra_object_id` (nullable today, populated when SSO lands) + `email` are both unique. `sender_user_id` in `sent_messages` is whatever the launch endpoint stamped — OID when SSO is live, email otherwise. Both are accepted by the Graph API URL (`POST /v1.0/users/{id-or-upn}/sendMail`).

---

## Tracking subsystem (canary scope)

The full deferred Phase 1 design is in [outreach_email.md §9](./outreach_email.md) and [the Phase 1 handoff §9](../.claude/plans/handoff-messaging-and-campaigns-phase1.md). What's actually in the codebase as of 2026-04-27:

### Files

- [`src/vacancysoft/outreach/tracking.py`](../src/vacancysoft/outreach/tracking.py) — pure module: token sign/verify, pixel injection, link rewriting, IP hashing, scanner heuristics. No I/O.
- [`src/vacancysoft/api/routes/tracking.py`](../src/vacancysoft/api/routes/tracking.py) — two unauthenticated endpoints (`/t/o/{token}`, `/t/c/{token}`).
- [`src/vacancysoft/worker/outreach_tasks.py`](../src/vacancysoft/worker/outreach_tasks.py) — `send_outreach_email` calls `inject_pixel` + `rewrite_links` immediately before handing the body to Graph. Mutates `row.body` so the as-sent body is what's persisted.
- [`alembic/versions/0013_add_tracking_tables.py`](../alembic/versions/0013_add_tracking_tables.py) — `open_events` and `click_events` tables, both indexed on `sent_message_id` and timestamp.
- [`tests/test_outreach_tracking.py`](../tests/test_outreach_tracking.py), [`tests/test_tracking_endpoints.py`](../tests/test_tracking_endpoints.py) — 38 + 14 tests respectively.

### Token format

```
<base64url(payload_json)>.<base64url(sig[:16])>
```

Payload: `{"m": "<sent_message_id>", "t": "o" | "c", "u": "<original_url>"?}`. Signature: first 16 bytes of HMAC-SHA256 over the payload bytes, key = `PROSPERO_TRACKING_SECRET`. No expiry — opens and clicks can legitimately happen months after send.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OUTREACH_TRACKING_ENABLED` | `true` | Kill switch. When `false`, injection is skipped — outbound bodies go through unchanged. The `/t/*` endpoints still run; existing pixels in already-sent mail keep working. |
| `TRACKING_DOMAIN` | `http://localhost:8000` | Base URL prepended when injecting tracking links. Becomes `https://link.barclaysimpson.com` (or BS-chosen subdomain) on Azure. |
| `PROSPERO_TRACKING_SECRET` | dev-only insecure default | HMAC secret. MUST be set in any non-dry-run / shared environment. IP-hash salt is derived from this with a domain separator label, so no separate var. |
| `TRACKING_FALLBACK_URL` | `https://www.barclaysimpson.com` | Where bad/forged click tokens redirect, so we don't leak token validity. |
| `[tracking]` section in `configs/app.toml` | — | `pixel_dedupe_window_seconds`, `scanner_pre_click_window_seconds`. Wired via the helpers in `outreach/tracking.py`. |

### Rollback

Two granularities:

- **Soft rollback** (preserves data, disables further injection):
  ```
  OUTREACH_TRACKING_ENABLED=false
  ```
  Restart API + worker. Outbound mail goes out untouched; existing pixels keep firing for already-sent mail; `/t/*` endpoints continue logging.

- **Full rollback** (drops tables, schema goes to pre-tracking state):
  ```
  alembic downgrade 0012
  ```
  Drops `open_events` + `click_events`. Application code referencing the models becomes dead but doesn't crash — the `/t/*` endpoints will 500 on insert. Pair with reverting the `tracking_routes.router` registration in `api/server.py` if you want a complete revert.

---

## Auth + identity (today)

### `api/auth.py` — `get_current_user(request, session)`

Resolution order:

1. **`X-MS-CLIENT-PRINCIPAL-NAME`** + `X-MS-CLIENT-PRINCIPAL-ID` headers (Easy Auth) — *not yet wired*; planned for Phase 1.
2. **`X-Prospero-User-Email`** request header → look up active user by email.
3. **Single-user-mode fallback** — if exactly one active user exists in the DB, use it. Lets dev work without any header plumbing.
4. Otherwise → 401 with bootstrap hint.

`PROSPERO_ADMIN_TOKEN` env var optionally gates admin-only endpoints (today only the users list/create routes). Retired by role-based access in Phase 1.

### Operator → mailbox routing

- Two registrations exist for every Prospero user: a **Prospero `users` row** (controlled by you) and a **`prospero-users` Entra group membership** (controlled by Keybridge).
- `sender_user_id` on `sent_messages` is the operator's Entra object-id (or UPN/email — Graph accepts either).
- Graph call shape: `POST /v1.0/users/{sender_user_id}/sendMail` with `saveToSentItems: true`. Mail leaves from that mailbox; replies come back via normal Exchange flow.
- Application Access Policy (set by Keybridge) restricts the Prospero app's mail permissions to **only** mailboxes of `prospero-users` group members. Anyone outside that group is unaffected by the app, even though the permissions are tenant-wide on paper.

---

## Configuration surface

### `configs/app.toml`

Read at startup by [`src/vacancysoft/settings.py`](../src/vacancysoft/settings.py) (and a few other points). Notable sections:

- `[intelligence]` — `dossier_model`, `campaign_model`, token budgets, voice template version
- `[outreach]` — poll cadence, poll max days, default cadence days
- `[tracking]` — pixel dedupe window, scanner pre-click window
- `[scoring]` — score thresholds and weights
- Per-source / per-adapter overrides

### Environment variables (master list)

See [`.env.example`](../.env.example) for the canonical list. Outreach + tracking specifics:

| Variable | Local default | Notes |
|---|---|---|
| `OUTREACH_DRY_RUN` | `true` | Master kill-switch for Graph calls. Routes through canned responses when truthy. |
| `OUTREACH_TRACKING_ENABLED` | `true` | Kill-switch for the injection path. Independent of `OUTREACH_DRY_RUN`. |
| `GRAPH_TENANT_ID` | unset | From Keybridge. |
| `GRAPH_CLIENT_ID` | unset | From Keybridge. |
| `GRAPH_CLIENT_SECRET` | unset | Dev-only path; on Azure this comes from Key Vault via `KEY_VAULT_URI`. |
| `KEY_VAULT_URI` | unset | When set, `SecretClient` reads from Key Vault instead of env. |
| `PROSPERO_TRACKING_SECRET` | unset (warn-and-default in dev) | HMAC secret. |
| `TRACKING_DOMAIN` | `http://localhost:8000` | Base URL for injected tracking links. |
| `TRACKING_FALLBACK_URL` | `https://www.barclaysimpson.com` | Bad-token click redirect. |

---

## Testing

Conventions across the outreach stack:

- In-memory SQLite (`StaticPool` + `check_same_thread=False`) when the test crosses thread boundaries via FastAPI's TestClient
- `_FakeRedis` records `enqueue_job` / `abort_job` calls; assertions check shape rather than wire format
- `_FakeGraphClient` returns canned send/list responses; tests covering the live path patch `outreach_tasks.GraphClient` to return a failing instance
- Pure-function tests (`test_outreach_tracking.py`) mock no DB
- 135+ tests across the outreach + tracking subsystems, all <2 seconds

Run the full outreach + tracking suite:

```bash
pytest tests/test_outreach_*.py tests/test_tracking_endpoints.py tests/test_campaigns_launch.py -W ignore::DeprecationWarning
```

---

## What's intentionally not in the codebase yet

Phase 1 work, scoped in [the Phase 1 handoff](../.claude/plans/handoff-messaging-and-campaigns-phase1.md):

- Easy Auth + first-login auto-provisioning
- `organization_id` tenancy seam
- Role enum on `users`, `require_role` dependency, retiring `PROSPERO_ADMIN_TOKEN`
- Admin Users page (`/admin/users`)
- Entra group membership automation (`outreach/group_client.py`)
- Campaigns tracker page wire-up to real data (`/campaigns` is currently a static mock gated behind `FEATURES.campaignsManager = false`)
- Leaver reconciliation cron
- Monitoring alerts wired to a notification channel
- Bicep templates for the Container App / Key Vault / Postgres / Redis stack

Future phases (post-BS rollout):

- Bullhorn CRM integration
- Merge fields / personalisation tokens
- Cohort / named-campaign data model (today campaigns are per-lead)
- Meeting-booked tracking
- NDR-bounce tracking (requires `Mail.Read` scope)
- SaaS multi-tenant product surface

---

## Operational concerns

### Local dev

`./start.sh` boots Redis, Postgres (Homebrew), the API on :8000, the worker, and Next.js on :3000. Tracking pixels point at `http://localhost:8000/t/...` — only reachable from your own machine. To validate tracking against a real inbox, either tunnel localhost (ngrok / Cloudflare Tunnel) or wait for Azure deploy.

### Deploying

[deployment_plan.md](./deployment_plan.md) covers the full Azure topology. [azure_deployment_delta.md](./azure_deployment_delta.md) is the focused checklist for taking the canary stack live.

### Rotation

Graph client secret: 180 days, manual rotation per [outreach_email.md §5.2](./outreach_email.md). Tracking HMAC secret: rotate alongside the Graph secret; old tokens become invalid (acceptable — opens/clicks past the rotation window aren't useful signal anyway).

### Incident playbook

See [outreach_email.md §5.3](./outreach_email.md) for the suspected-secret-leak flow. For tracking-specific incidents (pixel endpoint flooded, scanner behaviour changes, false-positive scanner flags spiking) — set `OUTREACH_TRACKING_ENABLED=false`, freeze the injection path, investigate from logs.

---

*Update history: 2026-04-21 (initial), 2026-04-27 (canary delta + tracking subsystem).*
