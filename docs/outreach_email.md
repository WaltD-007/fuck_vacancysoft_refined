# Outreach email — Microsoft Graph integration

> **Status (2026-04-21)**: PR A + B + C merged. System is fully built and runs end-to-end in **dry-run mode** (no real Graph calls). Production activation is gated on (a) Keybridge security-team approval, (b) Entra app registration + consent, and (c) flipping `OUTREACH_DRY_RUN=false`. PR D (Campaign Builder wiring) and PR E (settings UI + Bicep + ops runbook) are post-launch follow-ups, queued in the launch plan (items C22–C26).

This document is the single source of truth for how Prospero's outreach email system works, what's needed to take it live, and what's still outstanding post-launch.

- **Picking this up cold (new Claude chat or new engineer)**: read **§0 Handoff brief** first. It's the abbreviated orientation with all the specific conventions and schema that aren't obvious from just reading the code.
- **Understanding the system**: sections **1–3**.
- **Production activation**: section **4**.
- **Ongoing operation**: sections **5–6**.

---

## 0. Handoff brief for the next Claude

Skip this section if you were part of the original build. If you're picking this work up cold — either in a fresh chat session or as a new engineer — read this first.

### 0.1 Where you are

- **PR A / B / C are merged** (commits bc7353d · 95683fe · 1f2b18b on main). See §1 "Capabilities shipped" for exactly what that means.
- **PR D and PR E are the remaining build work** — specs in §6. Both are safe to build and land before Keybridge approval because the whole stack is gated on `OUTREACH_DRY_RUN=true` (the default).
- **Go-live itself is operator work**, not build work — §4 is a step-by-step for when Keybridge approval lands. Don't confuse "finish the build" with "go live."

### 0.2 Read this order before you write any code

1. §2.3 — the code layout table. It tells you exactly where each existing piece lives and where new PR D / PR E files should go.
2. `src/vacancysoft/outreach/` — three modules (`dry_run.py`, `secret_client.py`, `graph_client.py`) + their tests. **These are the house style**; match their patterns, their logging shape, their async-with-injectable-http-client testability. Don't invent new patterns.
3. `src/vacancysoft/worker/outreach_tasks.py` — `schedule_outreach_sequence` is the function PR D's new API endpoint will call. Read its signature before writing the endpoint so the schemas match.
4. **Appendix C — decision log**. Do NOT revisit the decisions already made there. Specifically: application permissions (not delegated), `Mail.ReadBasic` (not `Mail.Read`), `conversationId` matching (not `In-Reply-To`), polling (not Graph webhooks), `client_secret` (not certificate), `OUTREACH_DRY_RUN=true` default (not false). Every one of those has a rationale captured; re-litigating them wastes everyone's time.

### 0.3 PR D — explicit schema + conventions

Things §6 leaves implicit. Treat these as binding unless you have a strong reason:

- **Endpoint**: `POST /api/campaigns/{campaign_output_id}/launch`
- **Request body**:
  ```json
  {
    "tone": "formal",
    "cadence_days": [0, 7, 14, 21, 28],
    "sender_user_id": "<entra-object-id-or-upn-of-operator>",
    "recipient_email": "<override-or-omit-to-use-dossier-HM>"
  }
  ```
  `cadence_days` optional — defaults to `configs/app.toml [outreach].default_cadence_days`. `recipient_email` optional — defaults to the highest-confidence hiring manager's email from the dossier if present, else 422.
- **Response body**:
  ```json
  {
    "status": "scheduled",
    "sent_message_ids": ["uuid-1", "uuid-2", "uuid-3", "uuid-4", "uuid-5"],
    "first_send_scheduled_for": "2026-04-21T14:00:00Z"
  }
  ```
- **Email-content source**: the 5 `{subject, body}` pairs come from `CampaignOutput.outreach_emails[tone]` — specifically `emails = dossier_sections["outreach_emails"][tone]` shaped as a list of 5 `{subject, body}` dicts (see `base_campaign.py` schema).
- **Validation**: reject if `campaign_output` is not found, if `tone` is not one of the six, if `outreach_emails[tone]` doesn't exist or isn't length-5, or if `sender_user_id` is empty.
- **Auth**: no change — the existing session-cookie auth the rest of `/api/*` uses. Prospero-users group membership is enforced by Application Access Policy at Graph call time, not at this endpoint.
- **UI convention**: the Campaign Builder already has a tone-chip row. Add a "Launch" button that appears next to the selected chip (not a separate modal). Post-launch sequence-status view: inline expand below the email preview, showing one row per sequence-index with timestamp + status chip (`pending → sent → replied → cancelled`). "Cancel Sequence" button lives inside that expanded view.
- **Cancel endpoint**: `POST /api/campaigns/{id}/cancel` calls `cancel_pending_sequence_manual()` (already in `worker/outreach_tasks.py`). Returns `{cancelled_count: N}`.
- **Status polling in the UI**: SWR auto-refreshes the sequence view every 30s while any row is `pending`. Stop polling once all rows are terminal.
- **Tests**: match the shape of `tests/test_outreach_tasks.py` — in-memory SQLite, fake Redis, fake Graph client. Don't spin up a real FastAPI client unless there's a test that already does.

### 0.4 PR E — file paths + non-obvious bits

- **Bicep module**: create `infra/outreach.bicep` as a standalone module. `infra/` doesn't exist yet — create it. Cross-reference `docs/deployment_plan.md` for the parent topology (Container Apps env, Postgres, Redis). The outreach module adds: the Key Vault resource, a secret placeholder, access-policy granting the Container App's system-assigned managed identity `get` on secrets, and the four env-var bindings on the Container App (`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `KEY_VAULT_URI`, `OUTREACH_DRY_RUN`). Default `OUTREACH_DRY_RUN=true` in the Bicep parameter so forgetting to flip it means "safe."
- **Monitoring alerts**: the four thresholds in §5.4. One KQL query per alert. Land them as `infra/outreach_alerts.bicep` or inline in `outreach.bicep`, your call.
- **Runbook** (`docs/runbook_outreach_email.md`): reorganise §5 of THIS doc for on-call use. Don't rewrite from scratch — the content is already correct, just restructure into "problem → diagnosis → fix" format. Add a decision tree at the top: "Send failures → check X; polling stalls → check Y; reply-cancel not firing → check Z."
- **Settings page** (`web/src/app/settings/outreach/page.tsx`): list `prospero-users` group membership (read-only — fetch from Graph via a new `/api/admin/prospero-users` endpoint that calls `GET /groups/{id}/members`). Per-user stats: queries on `sent_messages` + `received_replies` grouped by `sender_user_id`. Self-test button: calls `POST /api/campaigns/test-send` which sends one email to the current operator's own address (no DB row created, just a spot-check).

### 0.5 What this build is NOT doing

So you don't add scope unprompted:

- No Graph webhooks / change notifications (polling is fine at our volume — see decision log)
- No Graph SDK dependency (we use `httpx` directly; SDK is heavy)
- No per-user OAuth / delegated permissions
- No multi-tenant support (single tenant, 3–5 users)
- No body or attachment access to received mail
- No Exchange distribution-list handling
- No calendar / contacts / any other Graph surface
- No customer-facing "unsubscribe" header (it's B2B outreach, recruiters already comply via their contracts)

### 0.6 If you hit something this doc doesn't cover

Order of precedence:
1. Check `src/vacancysoft/outreach/graph_client.py` source — its docstrings cover most edge cases
2. Check Appendix B (Graph request shapes) and C (decision log)
3. Check `docs/deployment_plan.md` for infra context
4. If still unclear, add a comment in your PR asking the question rather than guessing — this doc should be updated when decisions are made, not silently re-decided in code.

---

## 1. What this delivers

Prospero generates recruitment-outreach emails via its Campaign Builder (five-step sequences × six tones per lead — see [base_campaign.py](../src/vacancysoft/intelligence/prompts/base_campaign.py)). Until now the "Launch Campaign" button has been a no-op for actual sending; this stack wires up real Microsoft Graph delivery.

**Capabilities shipped in PR A/B/C:**

1. Send outreach emails on behalf of 3–5 named operators via Graph's `POST /users/{id}/sendMail` application-permission endpoint.
2. Schedule the 5-email sequence as deferred ARQ jobs (send 1 immediately, send 2 at +1 week, etc.) — configurable cadence per campaign.
3. Poll each conversation's inbox every 10 minutes for replies via `GET /users/{id}/messages?$filter=conversationId eq '...'`.
4. When a reply is detected, **automatically cancel remaining scheduled sends** in that sequence and flag the lead "Replied" in the UI.
5. Full audit trail: every Graph API call is logged with timestamp, actor user, operation, HTTP status, latency, and (for sends) returned message-id + conversation-id.
6. **All above runs in dry-run mode by default** — no real Graph calls until `OUTREACH_DRY_RUN=false` is set in environment. Safe to deploy and test without tenant permissions.

**Out of scope for PR A/B/C (queued as PR D/E post-launch):**

- Campaign Builder UI button actually triggering the sends (currently schedules in DB + ARQ; a small wire-up remains to connect the UI action). PR D.
- Settings page showing `prospero-users` group membership + "last sync" status. PR E.
- Bicep resources for Key Vault + Container App env variables. PR E.
- Full ops runbook (incident response, secret rotation, user onboarding/offboarding). PR E.

---

## 2. Architecture

### 2.1 Component map

```
┌────────────────────────────────────────────────────────────────────────┐
│  Prospero API (FastAPI, Container App)                                │
│                                                                        │
│  POST /api/campaigns/{id}/launch                                      │
│       │                                                                │
│       ▼                                                                │
│  enqueue schedule_outreach_sequence(campaign_id, user_id, tone) ──────┼──┐
└────────────────────────────────────────────────────────────────────────┘  │
                                                                            │
┌────────────────────────────────────────────────────────────────────────┐ │
│  Redis (ARQ queue)                                                    │ │
│                                                                        │ │
│  ┌──────────────────────────┐  ┌────────────────────────────────────┐│ │
│  │ Immediate queue          │  │ Deferred queue (Sorted Set)        ││ │
│  │  - send_outreach_email   │  │  - send_outreach_email (+7d)       ││◀┘
│  │  - poll_replies_for_conv │  │  - send_outreach_email (+14d)      ││
│  │                          │  │  - send_outreach_email (+21d)      ││
│  └──────────────────────────┘  │  - send_outreach_email (+28d)      ││
│                                 │  - poll_replies_for_conv (+10m, ∞) ││
│                                 └────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────────┘
                                                                            
┌────────────────────────────────────────────────────────────────────────┐
│  Worker (ARQ consumer, Container App — separate scale unit)          │
│                                                                        │
│  send_outreach_email(sent_message_id)                                 │
│   │                                                                   │
│   ├─> GraphClient.send_mail(user_id, to, subject, body)               │
│   │       │                                                           │
│   │       └─> POST /oauth2/v2.0/token  (cached, refreshed)            │
│   │       └─> POST /v1.0/users/{id}/sendMail                          │
│   │       └─> returns {message_id, conversation_id}                   │
│   │                                                                   │
│   └─> UPDATE sent_messages SET sent_at=..., conversation_id=...       │
│                                                                       │
│  poll_replies_for_conversation(sent_message_id)  (every 10 min)      │
│   │                                                                   │
│   ├─> GraphClient.list_replies(user_id, conversation_id, since)       │
│   │       └─> GET /v1.0/users/{id}/messages?$filter=...               │
│   │                                                                   │
│   ├─> If reply found:                                                 │
│   │     - INSERT received_replies                                     │
│   │     - ARQ delete_job() on remaining sequence items                │
│   │     - UPDATE campaign_output SET status='replied'                 │
│   │                                                                   │
│   └─> Else: re-enqueue self at +10m (bounded to 90 days)              │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│  Postgres (Azure DB for PostgreSQL Flexible, private endpoint)        │
│                                                                        │
│  sent_messages       — one row per sendMail call                      │
│  received_replies    — one row per reply Graph returned               │
│  campaign_outputs    — existing; gains lifecycle status column        │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Key Vault / secret flow

```
  Container App
    ↓ managed identity (system-assigned)
  Azure Key Vault  (same RG, same region)
    ├─ secret: prospero-graph-client-secret
    │    value: the Entra app-reg client secret
    │    rotation: manual, 180-day cadence
    └─ secret: prospero-graph-tenant-id
         value: tenant GUID (not sensitive, but stored alongside for atomicity)
```

Managed identity is granted `get` on these two secrets only. No human has runtime read access.

### 2.3 Code layout

```
src/vacancysoft/outreach/
├── __init__.py
├── secret_client.py      # Key Vault OR env-var secret fetch
├── graph_client.py       # Async Graph API wrapper
├── dry_run.py            # Canned responses for OUTREACH_DRY_RUN=true
├── models.py             # SentMessage, ReceivedReply SQLAlchemy models (PR B)
└── tasks.py              # send_outreach_email, poll_replies_for_conversation,
                          # schedule_outreach_sequence (PR C)

alembic/versions/
└── 0008_add_outreach_tables.py   # sent_messages, received_replies (PR B)

tests/
├── test_outreach_graph_client.py
├── test_outreach_secret_client.py
├── test_outreach_dry_run.py
├── test_outreach_models.py       (PR B)
└── test_outreach_tasks.py        (PR C)

configs/app.toml
└── [outreach] section             # Endpoint URLs, polling cadence, cancellation policy
```

### 2.4 Data model (PR B)

**`sent_messages`** — one row per outbound Graph call.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `campaign_output_id` | FK → campaign_outputs | ties back to the generated tone/arc |
| `sender_user_id` | string | Entra object-id of the operator who is sending (must be in `prospero-users` group at send time) |
| `recipient_email` | string | extracted from dossier `hiring_managers[0]` at schedule time |
| `sequence_index` | int (1-5) | which of the 5-sequence arc |
| `tone` | string | formal / informal / … |
| `scheduled_for` | timestamptz | when ARQ should fire |
| `sent_at` | timestamptz nullable | populated by worker on success |
| `graph_message_id` | string nullable | Graph's `id` field from sendMail response |
| `conversation_id` | string nullable | Graph's `conversationId` — the reply-match key |
| `status` | string | `pending` / `sent` / `cancelled_replied` / `cancelled_manual` / `failed` |
| `error_message` | text nullable | populated on `failed` |
| `arq_job_id` | string | ARQ job id so we can delete/cancel it |
| `subject` | string | at-rest copy of what was sent (or would have been, in dry-run) |
| `body` | text | same |
| `created_at`, `updated_at` | timestamptz | |

**`received_replies`** — one row per Graph-observed reply. Many-to-one with `sent_messages` via `conversation_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `conversation_id` | string, indexed | matches `sent_messages.conversation_id` |
| `sender_user_id` | string | which of our users' mailboxes the reply was observed in |
| `graph_message_id` | string | unique Graph id for the reply |
| `from_email` | string | the replier |
| `received_at` | timestamptz | from `receivedDateTime` |
| `subject` | string | |
| `matched_sent_message_id` | FK → sent_messages nullable | best-match sent-message in this conv |
| `created_at` | timestamptz | |

Note: no mail body or attachments are stored — `Mail.ReadBasic` doesn't expose them, and we don't need them for reply detection.

### 2.5 Sequence lifecycle

1. Operator clicks **Launch Campaign** in UI (PR D).
2. API calls `schedule_outreach_sequence(campaign_output_id, tone, cadence_days=[0, 7, 14, 21, 28])`.
3. Five `sent_messages` rows created, status `pending`, with `arq_job_id` set to each deferred ARQ job.
4. ARQ fires `send_outreach_email` at each scheduled time.
5. Each worker invocation:
   - Re-reads `sent_messages` row (may have been cancelled since scheduling → exit early)
   - Calls `GraphClient.send_mail()`
   - Updates row: `status='sent'`, populates `sent_at`, `graph_message_id`, `conversation_id`
   - Enqueues `poll_replies_for_conversation(sent_message_id)` at +10 minutes
6. Poller runs every 10 minutes (bounded to 90 days, after which polling stops):
   - Reads all `sent_messages` for this `conversation_id` (so we poll once per conv, not per message)
   - Calls `GraphClient.list_replies(user_id, conversation_id, since=earliest_sent_at)`
   - Any new reply → insert `received_replies`, then:
     - Find all `sent_messages` in this conv with `status='pending'` and `sequence_index > matched.sequence_index`
     - For each: ARQ `delete_job(arq_job_id)`, set `status='cancelled_replied'`
     - Update `campaign_outputs.status='replied'`
   - Else: re-enqueue self at +10m

### 2.6 Permissions (exact scope requested from Keybridge)

| Graph permission | Type | Purpose |
|---|---|---|
| `Mail.Send` | Application | `POST /users/{id}/sendMail` |
| `Mail.ReadBasic` | Application | `GET /users/{id}/messages?$filter=conversationId eq ...` (metadata only, no body/attachments) |

Scoped tenant-side via Exchange Online **Application Access Policy** to Entra group `prospero-users`:

```powershell
New-ApplicationAccessPolicy `
  -AppId <client-id> `
  -PolicyScopeGroupId prospero-users@<domain> `
  -AccessRight RestrictAccess `
  -Description "Prospero — restrict Graph Mail.* to prospero-users members only"
```

---

## 3. Dry-run mode (the default)

Every code path in this stack routes through `OUTREACH_DRY_RUN`. When the env var is unset or `true`:

- `GraphClient.send_mail()` returns a canned `(message_id, conversation_id)` synthesised from `uuid.uuid4()`
- `GraphClient.list_replies()` returns an empty list (no replies ever fire, so no cancellation ever triggers)
- `SecretClient.get_client_secret()` returns the literal string `"DRY_RUN_SECRET"` without touching Key Vault or env
- All DB writes still happen (so the UI lifecycle works end-to-end)
- All ARQ scheduling still happens
- All logging still happens — but log lines are marked `dry_run=true` in structured JSON

This means we can:
- **Deploy to production now**, before Keybridge approves anything
- Test the UI flow end-to-end (operator clicks Launch Campaign → rows appear → ARQ fires → status transitions to `sent` with a fake message_id)
- Demonstrate the system to security review with zero risk of accidentally sending real mail
- Flip one env var to go live on the day of approval

Flipping live requires **all** of these to be true:

| Condition | Verify with |
|---|---|
| Entra app registered, consent granted | Entra admin portal |
| `prospero-users` group exists, contains 3–5 operators | `Get-MgGroupMember -GroupId <id>` |
| Application Access Policy applied | `Get-ApplicationAccessPolicy -Identity <appid>` |
| Client secret in Key Vault | `az keyvault secret show --name prospero-graph-client-secret` |
| Container App has managed-identity access to Key Vault | Azure portal → Key Vault → Access policies |
| Env vars set on Container App: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `KEY_VAULT_URI` | `az containerapp show` |
| `OUTREACH_DRY_RUN=false` on Container App | `az containerapp update --set-env-vars OUTREACH_DRY_RUN=false` |

**Rollback at any point**: set `OUTREACH_DRY_RUN=true` and restart the Container App. All subsequent sends become fake; any already-sent mail stays in Exchange audit logs, any already-scheduled ARQ jobs become no-ops on fire.

---

## 4. Pre-launch checklist

Do these **in order**. Don't skip ahead; each step depends on the previous.

### 4.1 External dependencies (you + Keybridge + tenant admin)

| # | Item | Owner | Estimate | How to verify |
|---|---|---|---|---|
| 1 | Security review complete with Keybridge | You → Keybridge | Days | Written approval received |
| 2 | Create Entra app registration (name: `Prospero`) | Tenant admin | 10 min | `az ad app show --id <name>` returns JSON |
| 3 | Add `Mail.Send` + `Mail.ReadBasic` application permissions | Tenant admin | 2 min | App manifest shows both |
| 4 | Global admin grants tenant-wide consent | Tenant admin | 2 min | Entra portal → API permissions all show "Granted" |
| 5 | Create Entra group `prospero-users`, add 3–5 operator UPNs | Tenant admin | 5 min | `Get-MgGroupMember` lists them |
| 6 | Run `New-ApplicationAccessPolicy` to scope the app to the group | Exchange admin | 5 min | `Get-ApplicationAccessPolicy` shows RestrictAccess policy |
| 7 | Test the policy: `Test-ApplicationAccessPolicy -Identity <member-upn> -AppId <appid>` returns `Granted`; same test with a non-member returns `Denied` | Exchange admin | 2 min | — |
| 8 | Client secret created (label: `prospero-prod-YYYY-MM-DD`, 180-day expiry) | Tenant admin | 2 min | Secret value shown once — save to Key Vault immediately |

### 4.2 Azure infrastructure (you)

| # | Item | Estimate |
|---|---|---|
| 9 | Key Vault exists in same RG as Container App | 2 min |
| 10 | Store client secret: `az keyvault secret set --vault-name <kv> --name prospero-graph-client-secret --value <secret>` | 1 min |
| 11 | Enable system-assigned managed identity on Container App: `az containerapp identity assign --system-assigned` | 1 min |
| 12 | Grant Container App managed identity `get` on Key Vault secrets: `az keyvault set-policy --name <kv> --object-id <app-identity-id> --secret-permissions get` | 2 min |
| 13 | Set Container App env vars: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `KEY_VAULT_URI` — leave `OUTREACH_DRY_RUN=true` for now | 2 min |

### 4.3 Code + DB (automated by PR A/B/C)

| # | Item | Where |
|---|---|---|
| 14 | Run migration 0008 (creates `sent_messages`, `received_replies`) | `alembic upgrade head` on first deploy |
| 15 | Verify worker picks up new task registrations | `arq --help` shows `outreach_tasks` in `WorkerSettings.functions` |
| 16 | Smoke-test dry-run: call `GraphClient.send_mail()` from a Python shell on the Container App → returns canned response | — |

### 4.4 Go-live (5 minutes, after everything above is green)

| # | Item | Reversibility |
|---|---|---|
| 17 | Launch one canary campaign in the UI with `OUTREACH_DRY_RUN=true` still set. Verify: `sent_messages` rows created, ARQ fires on schedule, `status='sent'` with fake `graph_message_id`, polling task enqueued. | Delete rows, no external effect |
| 18 | Set `OUTREACH_DRY_RUN=false` and restart the Container App | Flip back to `true` |
| 19 | Launch a second canary campaign to a **single real recipient** (yourself). Verify: email received, `conversation_id` populated, replying to it causes poller to insert `received_replies` row and cancel the pending sequence items within ~10 min. | Reply with "STOP"; poller will pick it up; remaining 4 sends auto-cancelled |
| 20 | Full go-live: operators can launch campaigns normally. | Flip `OUTREACH_DRY_RUN=true` to freeze the system |

---

## 5. Ongoing operation

### 5.1 Adding / removing a Prospero operator

1. Add/remove the user's UPN from the `prospero-users` Entra group. Change propagates within ~30 min via Application Access Policy.
2. In Prospero DB: update the `users` table (`active=true/false`). UI will show/hide them immediately.
3. **Do not** modify the Entra app permissions — they stay tenant-wide on paper; the group + policy is the actual gate.

### 5.2 Rotating the client secret

Manual 180-day cadence (calendar reminder).

```bash
# 1. Generate new secret in Entra (copy the value once)
az ad app credential reset --id $GRAPH_CLIENT_ID --append --years 0 --display-name "prospero-prod-$(date +%Y-%m-%d)"

# 2. Save the new value to Key Vault (creates a new version, old version stays available)
az keyvault secret set --vault-name <kv> --name prospero-graph-client-secret --value '<new secret>'

# 3. Restart Container App to pick up the new secret version
az containerapp revision restart --name prospero-api --resource-group <rg>
az containerapp revision restart --name prospero-worker --resource-group <rg>

# 4. Monitor logs for 30 min to confirm sends + polls still working

# 5. Delete the old credential in Entra (only once confirmed working)
az ad app credential delete --id $GRAPH_CLIENT_ID --key-id <old-key-id>
```

### 5.3 Incident response — suspected secret leak

```bash
# STEP 1: emergency freeze (30 seconds)
az containerapp update --name prospero-api   --set-env-vars OUTREACH_DRY_RUN=true
az containerapp update --name prospero-worker --set-env-vars OUTREACH_DRY_RUN=true
# Further sends are now fake. Already-sent mail can't be recalled, but no NEW sends fire.

# STEP 2: revoke the leaked secret (2 minutes)
az ad app credential delete --id $GRAPH_CLIENT_ID --key-id <leaked-key-id>
# App can no longer authenticate at all. Even if attacker has the secret, it's dead.

# STEP 3: audit the blast radius (10-30 minutes)
# Check Exchange admin audit logs for all sendMail events from the app identity in the leak window.
# Check our sent_messages table: SELECT * FROM sent_messages WHERE sender_user_id NOT IN ('<expected-users>')
#   — should return zero rows. If non-zero, the Application Access Policy is malfunctioning.

# STEP 4: regenerate + restore service (5 minutes)
# Follow the rotation procedure in 5.2 with a fresh secret.
# Flip OUTREACH_DRY_RUN back to false on both Container Apps.
```

### 5.4 Monitoring + alerts

Built into PR A's structured logging. Watch these in Log Analytics:

| Signal | Threshold | Action |
|---|---|---|
| Graph 401/403 rate | >1% over 5 min | Check policy hasn't been reverted; check secret hasn't expired |
| `send_outreach_email` ARQ failure rate | >5% over 1h | Check user membership in `prospero-users`; check mailbox quota |
| `poll_replies_for_conversation` running > 200 concurrent instances | Sustained | Scale worker up, or reduce polling cadence from 10m to 30m |
| `OUTREACH_DRY_RUN=true` in prod env | Any | Loud alert — either intentional freeze or misconfig |

A pre-built KQL snippet for each lives in `docs/monitoring_queries.md` (created as part of PR E).

---

## 6. Post-launch follow-ups (PR D + PR E)

These are not on the critical path — the system works without them — but they're needed before you'd offer the platform to colleagues beyond the initial 3–5.

### PR D — Campaign Builder UI wiring (~300 lines, 1 day)

Currently: Campaign Builder generates the email sequences (five tones × six sequences = 30 emails per lead) and writes them to `campaign_outputs.outreach_emails`. The "Launch Campaign" button is a no-op.

**What PR D adds:**

- New endpoint `POST /api/campaigns/{id}/launch` — takes `{tone: "formal", cadence_days: [0, 7, 14, 21, 28]}`, calls `schedule_outreach_sequence()`, returns `{sent_message_ids: [...], poll_job_id: ...}`.
- Frontend:
  - Tone selector on each lead (currently shows all 6 — need to pick one to launch)
  - Cadence selector (dropdown: "weekly / fortnightly / custom")
  - "Launch Campaign" button wired to the new endpoint
  - Post-launch view: the 5-email sequence with timestamps and status chips (`pending → sent → replied → cancelled`)
  - "Cancel Sequence" button that marks all `pending` rows as `cancelled_manual` and deletes their ARQ jobs

### PR E — Settings + Bicep + runbook (~500 lines, 1 day)

**What PR E adds:**

1. **Settings page** (`web/src/app/settings/outreach/page.tsx`):
   - List the `prospero-users` group membership (read from Graph via a separate read-only endpoint)
   - "Last Key Vault sync" timestamp
   - Per-user stats: messages sent this month, reply rate, last-send timestamp
   - Button to test a single send (to yourself) without going through the full scheduler — useful for verifying post-deploy
2. **Bicep template** (`infra/outreach.bicep`):
   - Key Vault resource + access policy for the Container App managed identity
   - Container App env var bindings for `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `KEY_VAULT_URI`, `OUTREACH_DRY_RUN`
   - Monitoring alerts (the four thresholds from §5.4)
3. **Ops runbook** (`docs/runbook_outreach_email.md`):
   - Expanded version of §5 of this doc, formatted for on-call use
   - Decision tree for common incidents (send failures / polling stalls / reply-cancel not firing)
   - KQL queries for every metric mentioned

---

## Appendix A — Environment variables reference

| Variable | Default | Purpose | Where set |
|---|---|---|---|
| `OUTREACH_DRY_RUN` | `true` | Master kill-switch. Any value other than `false` (case-insensitive) routes all calls through the dry-run module. | Container App env |
| `GRAPH_TENANT_ID` | *(none)* | Entra tenant GUID. | Container App env |
| `GRAPH_CLIENT_ID` | *(none)* | Entra app-registration GUID. | Container App env |
| `KEY_VAULT_URI` | *(none)* | `https://<vault>.vault.azure.net` — enables Key Vault secret fetch path. Absence routes to `GRAPH_CLIENT_SECRET` env-var path (dev-only). | Container App env |
| `GRAPH_CLIENT_SECRET_NAME` | `prospero-graph-client-secret` | Secret name within the Key Vault. | Container App env |
| `GRAPH_CLIENT_SECRET` | *(none)* | **Dev only.** Overrides Key Vault path if `KEY_VAULT_URI` is unset. Never set this in production. | Local `.env` only |
| `OUTREACH_POLL_INTERVAL_MIN` | `10` | Minutes between reply polls per conversation. | `configs/app.toml [outreach]` |
| `OUTREACH_POLL_MAX_DAYS` | `90` | Stop polling a conversation after this many days since first send. | `configs/app.toml [outreach]` |
| `OUTREACH_DEFAULT_CADENCE_DAYS` | `[0, 7, 14, 21, 28]` | Default day-offsets for the 5-sequence schedule. UI can override per-campaign. | `configs/app.toml [outreach]` |

## Appendix B — Graph request shapes (for reference)

### sendMail

```http
POST /v1.0/users/{user-id-or-upn}/sendMail
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "message": {
    "subject": "...",
    "body":    {"contentType": "HTML", "content": "..."},
    "toRecipients": [{"emailAddress": {"address": "..."}}]
  },
  "saveToSentItems": true
}

→ 202 Accepted
Response body is empty. Message id is NOT returned directly.
Workaround: we use /createDraft + /send or read sent items
immediately after via /users/{id}/mailFolders/sentitems/messages.
```

### list replies by conversation

```http
GET /v1.0/users/{user-id-or-upn}/messages?
  $filter=conversationId eq 'AAQkADAwATNiZmYA...' and receivedDateTime gt 2026-04-21T10:00:00Z
  &$select=id,conversationId,from,receivedDateTime,subject,internetMessageId
  &$top=25
Authorization: Bearer <access-token>

→ 200 OK
{
  "value": [
    {
      "id": "...",
      "conversationId": "AAQkADAwATNiZmYA...",
      "from": {"emailAddress": {"address": "hm@company.com", "name": "..."}},
      "receivedDateTime": "2026-04-21T14:32:00Z",
      "subject": "Re: ..."
    }
  ]
}
```

## Appendix C — Decision log

| Decision | Alternative considered | Why we chose this |
|---|---|---|
| `Mail.ReadBasic` (not `Mail.Read`) | `Mail.Read` for full body access | `conversationId` + receivedDateTime is all we need for reply detection. Narrower scope is an easier security-review sell. |
| `conversationId` matching | `In-Reply-To` header parsing | `Mail.ReadBasic` doesn't expose headers; conversationId is native to Graph and reliable across clients. |
| Application permissions | Delegated (per-user OAuth) | No per-user consent flow; worker can send on behalf of an offline user; scoped via Application Access Policy so blast radius is identical. |
| Client-credentials flow, client_secret | Certificate-based auth | Certs are a nicer security posture but add rotation machinery. Plumbing leaves room to swap (`GraphCredential.from_cert()`); swappable ~20min of work. |
| Key Vault secret storage | Env var only | Env vars leak into logs, container images, telemetry. Key Vault + managed identity = no human has runtime read. |
| `OUTREACH_DRY_RUN=true` default | Default `false` | Safer default. A misconfigured prod deploy sends fake mail, not real mail. |
| Per-conversation polling every 10 min | Webhook subscriptions (Graph change notifications) | Webhooks require a public HTTPS endpoint + subscription renewals every 3 days. Polling is simpler for 20 msg/day volume. Revisit at 1000 msg/day. |
| Cancel remaining sequence on reply | Let sequence finish regardless | Recruiter best practice is to stop outreach once reply lands — that's the whole point of polling. UI surfaces `cancelled_replied` status distinctly from manual cancels. |

## Appendix D — Related documents

- **[Security brief to Keybridge](./outreach_security_brief.md)** — the document sent to IT to initiate the security review (generated in a separate PR).
- **[deployment_plan.md](./deployment_plan.md)** — overall Azure topology; outreach is an extension of that plan.
- **[launch_plan.md](./launch_plan.md)** — items C22–C26 track the outreach rollout.
- **[TODO.md](./TODO.md)** — individual ticket rationale for anything not captured above.

---

*Last updated: 2026-04-21 post PR C merge.*
