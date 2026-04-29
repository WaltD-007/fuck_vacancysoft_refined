# Local canary → Azure deployment delta

> **Status (2026-04-27)**: planning. Today the canary runs locally — Builder Launch button works, dry-run smoke passes, only blocker to live mail is the `.env` Graph credentials from Keybridge. This doc is the operator's checklist for taking that same stack to Azure.

This is a **delta** doc, not a full deployment guide. It assumes you've read:

- [deployment_plan.md](./deployment_plan.md) — the approved Azure Container Apps topology (web + api + worker + Postgres + Redis + Front Door + Easy Auth)
- [outreach_email.md](./outreach_email.md) — PR A/B/C architecture, Key Vault flow, secret rotation
- [.claude/plans/handoff-messaging-and-campaigns-phase1.md](../.claude/plans/handoff-messaging-and-campaigns-phase1.md) — the BS rollout build (Easy Auth, tracking, admin UI, tenancy seam)

Scope: only the changes specific to taking the local canary live on Azure. Most of the application code is already deployment-clean.

---

## What you have today (recap)

Local-only stack:

- Postgres 17 on `localhost`, DB `prospero`
- Redis on `localhost:6379` (Homebrew)
- FastAPI on `:8000`, started by `start.sh`
- ARQ worker as separate process, same script
- Next.js dev server on `:3000`, same script
- `.env` file holds all secrets including (or about to hold) `GRAPH_CLIENT_SECRET`
- `OUTREACH_DRY_RUN=true` until Keybridge sends the secret
- Identity is single-user-fallback in [api/auth.py](../src/vacancysoft/api/auth.py) — no Easy Auth, no SSO, no header
- Single-tenant — all `users` rows belong to BS implicitly, no `organization_id` column
- Launch + Cancel endpoints live ([api/routes/campaigns.py](../src/vacancysoft/api/routes/campaigns.py))
- Tracking (open/click pixels) NOT yet built

---

## Code changes needed for Azure

Most of the codebase is already deployment-clean — the abstractions in [secret_client.py](../src/vacancysoft/outreach/secret_client.py) and [worker/settings.py](../src/vacancysoft/worker/settings.py) were built with both paths in mind. The actual code delta is small.

### Required for Azure deploy

| File / area | Change | Reason |
|---|---|---|
| [web/src/app/lib/swr.ts:8](../web/src/app/lib/swr.ts) | `API` constant — already does `process.env.NEXT_PUBLIC_API_BASE \|\| "/api"`. Just verify the env var is set to `/api` in the Container App so the same-origin pattern holds behind Front Door. | Same-origin proxy via Front Door. |
| [configs/app.toml](../configs/app.toml) | `database_url = "postgresql://localhost/prospero"` is currently a literal. Either template at image-build time or read from `DATABASE_URL` env var (preferred — handful of call sites). | Local hostname doesn't resolve in a container. |
| [api/auth.py](../src/vacancysoft/api/auth.py) | Add a branch reading `X-MS-CLIENT-PRINCIPAL-NAME` (Easy Auth's UPN header) before the existing `X-Prospero-User-Email` path. First-login auto-provisions a `User` row from the SSO claims. ~20 lines + a test. | Easy Auth is the production auth; the existing single-user-fallback is dev-only. |
| [start.sh](../start.sh) | Don't use it in containers. Each Container App has its own entrypoint command (see below). | macOS-isms (`brew services`, `lsof`, `pkill`) won't run in Linux. |

### Net-new for Phase 1 (specced in the handoff, all post-canary)

- `outreach/group_client.py` — Entra group membership automation (when service principal owns `prospero-users`)
- `outreach/tracking.py` + `api/routes/tracking.py` + migration 0014 — open/click pixels
- `api/routes/admin.py` + `web/src/app/admin/users/page.tsx` — admin UI
- Tenancy seam: `organizations` table (migration 0013), `organization_id` columns, `get_tenant_config(org_id)` shim
- Role enum (`operator` / `org_admin` / `super_admin`), retire `PROSPERO_ADMIN_TOKEN`

None of these are needed for canary, but they all go in before the BS rollout. See the Phase 1 handoff for PR-by-PR breakdown.

---

## Configuration changes

### Environment variables

`.env` becomes Container App env vars (set via Bicep or `az containerapp update --set-env-vars`).

| Variable | Local (`.env`) | Azure (Container App) | Notes |
|---|---|---|---|
| `DATABASE_URL` | `postgresql://localhost/prospero` | `postgresql://prospero@<server>.postgres.database.azure.com:5432/prospero?sslmode=require` | Or pulled from Key Vault. |
| `REDIS_URL` | `redis://localhost:6379` | `rediss://<cache>.redis.cache.windows.net:6380?password=<key>` | TLS port 6380, password in Key Vault. |
| `OPENAI_API_KEY` | direct value | Key Vault reference | Already env-driven. |
| `GRAPH_TENANT_ID` | direct value | direct value (not sensitive) | Same. |
| `GRAPH_CLIENT_ID` | direct value | direct value (not sensitive) | Same. |
| `GRAPH_CLIENT_SECRET` | **direct value** | **REMOVED** — read from Key Vault via `KEY_VAULT_URI` | The dev-only env-var path stops here. |
| `KEY_VAULT_URI` | unset | `https://<vault>.vault.azure.net` | Triggers the Key Vault path in [secret_client.py](../src/vacancysoft/outreach/secret_client.py). |
| `GRAPH_CLIENT_SECRET_NAME` | unset | `prospero-graph-client-secret` | Default name; override only if you rename it in KV. |
| `OUTREACH_DRY_RUN` | `false` (when live) | **`true` initially** then flip after canary | Bicep parameter default; safer to land deployment with dry-run on. |
| `NEXT_PUBLIC_API_BASE` | unset (defaults to `/api`) | `/api` | Confirms same-origin proxy. |

### What stays the same

- `configs/app.toml [outreach]` section (poll interval, cadence, max days). Phase 1 may bump `poll_interval_minutes` from 10 → 30 for 30-user scale ([§8.1 of the handoff](../.claude/plans/handoff-messaging-and-campaigns-phase1.md)).
- All ARQ task signatures, all DB schemas, all API contracts.
- The Builder UI, Launch button, dry-run path.

### `configs/app.toml` `database_url` fix

Concrete change: replace the hardcoded line with a `DATABASE_URL` env-var read in [src/vacancysoft/settings.py](../src/vacancysoft/settings.py). Three call sites total (`settings.py`, `worker/settings.py`, `intelligence/dossier.py`) — `get_settings()` is already the indirection point, so it's a one-line change in one file.

---

## Infrastructure that needs to exist

Per [deployment_plan.md](./deployment_plan.md) topology. Quick checklist of resources:

| Resource | Purpose | Sizing |
|---|---|---|
| Resource Group | container for everything | one |
| VNet | private networking | /24 minimum |
| Container Apps Environment | hosts the three apps | one |
| Container App: `prospero-api` | FastAPI on port 8000, ingress on `/api/*` | 1–3 replicas, 1 vCPU / 2 GB |
| Container App: `prospero-web` | Next.js on port 3000, ingress on `/` | 1 replica, 0.5 vCPU / 1 GB |
| Container App: `prospero-worker` | ARQ, no ingress | 1–N replicas, 1 vCPU / 2 GB |
| Container Apps Job: `prospero-migrate` | one-shot Alembic upgrade | runs on deploy |
| Azure Database for PostgreSQL Flexible Server | data | B2s burstable, 32 GB initially |
| Azure Cache for Redis | ARQ queue | Basic C0 (250 MB) — enough for 30 users |
| Azure Container Registry (or Docker Hub) | image hosting | Basic SKU |
| Key Vault | secrets | Standard, RBAC mode |
| Front Door / App Gateway | TLS, WAF, single domain | Front Door Standard |
| Log Analytics workspace | container stdout + alerts | one |
| Application Insights | trace + correlation | linked to LA workspace |
| Custom domain | `prospero.barclaysimpson.com` (or similar) | DNS owned by BS IT |
| Tracking subdomain (Phase 1, not canary) | `link.barclaysimpson.com` CNAMEd to the api Container App | when tracking lands |

Bicep templates land as PR P10 in the Phase 1 handoff. They don't exist yet.

---

## Pre-deploy checklist

In order. Each blocks the next.

1. **Container images built** — three Dockerfiles (`api/`, `web/`, `worker/`). Pushed to ACR. Tag with git SHA.
2. **Resource group + VNet provisioned** (one-time, Bicep parameters set).
3. **Postgres provisioned** with private endpoint into the VNet, firewall closed to public, Entra-admin set up. Connection string in Key Vault.
4. **Redis provisioned**, TLS-only (port 6380), private endpoint into the VNet. Primary key in Key Vault.
5. **Key Vault created**, RBAC mode. Secrets uploaded:
   - `prospero-graph-client-secret` (the value from Keybridge)
   - `prospero-database-url`
   - `prospero-redis-url`
   - `prospero-openai-api-key`
6. **Container Apps Environment** provisioned, VNet-integrated.
7. **Each Container App** has system-assigned managed identity + Key Vault `get` on secrets. Verify with `az keyvault secret show` from inside the container.
8. **Migration job** runs `alembic upgrade head` against the live DB once before any API replicas come up.
9. **Front Door** with custom domain + Managed Cert. Backend pool: api Container App (`/api/*`) + web Container App (`/`). Easy Auth configured for Microsoft identity provider.
10. **Easy Auth carve-out for tracking endpoints** (`/t/*`) — set unauthenticated action to `allow anonymous` for that path prefix only. Required when Phase 1 tracking lands; can omit for canary-only deploy.

---

## Deploy sequence (canary-only deploy)

Strip the Phase 1 admin/tracking work for the moment — this is the minimum to take today's working canary to Azure.

1. Land the small code changes from "Code changes needed" above (auth.py Easy Auth branch, app.toml DATABASE_URL, NEXT_PUBLIC_API_BASE confirmation). One PR.
2. Build + push images to ACR.
3. Provision infra via Bicep (or click-ops for the canary; Bicep can come with PR P10).
4. Run the migration job.
5. Deploy worker first (no ingress, safe to be wrong).
6. Deploy api second.
7. Deploy web last — once api is responding.
8. **Verify with `OUTREACH_DRY_RUN=true` still on**:
   - Hit the app URL, sign in via Easy Auth, see the Builder
   - Pick a lead, click Launch — same flow as local dry-run
   - Verify in Postgres: `SELECT status FROM sent_messages ORDER BY created_at DESC LIMIT 5` shows `pending → sent` flips with fake message IDs
9. **Flip live**:
   ```bash
   az containerapp update -n prospero-api    --set-env-vars OUTREACH_DRY_RUN=false
   az containerapp update -n prospero-worker --set-env-vars OUTREACH_DRY_RUN=false
   ```
10. Run the canary against your own UPN. Same as local: send → arrives in your inbox → reply → poller cancels remaining 4 within 10 min.

---

## Rollback

At every stage, the rollback is the same:

```bash
az containerapp update -n prospero-api    --set-env-vars OUTREACH_DRY_RUN=true
az containerapp update -n prospero-worker --set-env-vars OUTREACH_DRY_RUN=true
```

Already-sent mail can't be recalled (Exchange retains the audit trail). Already-scheduled deferred sends become no-ops on fire — the worker checks `OUTREACH_DRY_RUN` per send and short-circuits to canned responses.

For data rollback: Postgres point-in-time restore (Flexible Server retains 7+ days). Run a fresh migration to a sister DB if you need to test a rollback.

---

## What this canary deploy does NOT include

Deferred to the post-canary build (see Phase 1 handoff):

- Tracking infrastructure (open/click pixels, tracking domain, HMAC tokens, `open_events` / `click_events` tables)
- Easy Auth `prospero-users` group enforcement at the door (we still rely on Application Access Policy at the Graph layer for now)
- Admin UI / Users page
- Tenancy seam (`organizations` table, `organization_id` columns)
- Role-based admin (`require_role` dependency)
- Leaver reconciliation cron
- Monitoring alerts wired to a notification channel
- Bicep templates committed to repo (PR P10)
- Runbook (PR P11)

The canary-only Azure deploy is a step you can take **between** today's local canary and the full Phase 1 build, OR you can defer all Azure work until Phase 1 is shipped and deploy the full stack in one go. Pick based on whether you want a public-URL canary first or are happy keeping the canary local until Phase 1 lands.

My recommendation: **don't deploy a canary-only Azure stack**. The deploy work has a real cost; doing it twice (once for canary, once after Phase 1) doubles the work. Run the canary locally, build Phase 1 in dry-run locally, deploy the whole thing to Azure once at the end. The local canary is the proof point that the Graph integration works — the Azure deploy adds infrastructure but no new application correctness signal.

---

## Open questions / decisions

Worth answering before any infra is provisioned:

1. **Domain name for the app** — `prospero.barclaysimpson.com`? Or a sub-app domain like `outreach.barclaysimpson.com`? BS IT owns the DNS.
2. **Tracking domain** — Phase 1 ask, but lead time on DNS is the same. Decide alongside the app domain.
3. **Postgres SKU** — B2s burstable is enough for 30 users; revisit at 100+. Confirm with cost estimates.
4. **Backups + point-in-time restore window** — default 7 days OK, or push to 30?
5. **Region** — UK South for BS? Or wherever the rest of their Azure footprint sits?

---

*Last updated: 2026-04-27. Canary delta complete. References [deployment_plan.md](./deployment_plan.md) and [.claude/plans/handoff-messaging-and-campaigns-phase1.md](../.claude/plans/handoff-messaging-and-campaigns-phase1.md).*
