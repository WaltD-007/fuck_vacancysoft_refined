# Prospero deployment plan — Azure Container Apps

## Context

`fuck_vacancysoft_refined` is a coverage-first job-scraping + recruitment-intelligence pipeline. Today it runs locally via `start.sh` which assumes macOS + Homebrew + local Postgres + local Redis + Playwright Chromium installed on the host. The user wants to deploy it for **internal-team use** on **Azure**, with **in-container Chromium**, **scheduled scraping**, a **single domain** fronting both the Next.js frontend and the FastAPI backend, and **simple SSO-style auth** added at the edge (no code changes to the API).

Output: a self-hostable, repeatable deployment with managed Postgres + Redis, secrets in a vault, and no macOS-isms.

---

## What currently exists (deployment-relevant)

### Runtime shape
- **FastAPI API** — `vacancysoft.api.server` on port 8000 (`src/vacancysoft/api/server.py:117`). Uvicorn. Four routers: leads, sources, add_company, campaigns. **CORS is `allow_origins=["*"]`** and **there is no auth whatsoever** — the deployed instance MUST sit behind an edge auth layer.
- **ARQ worker** — `vacancysoft.worker.settings.WorkerSettings` (`src/vacancysoft/worker/settings.py:44`). Two registered jobs: `process_lead` and `scrape_source` (`src/vacancysoft/worker/tasks.py`). Max concurrent 25, job timeout 900 s, 3 retries. Self-heals stuck `ReviewQueueItem` rows at startup (`src/vacancysoft/worker/self_heal.py`).
- **Next.js 16 frontend** — `web/`. All pages are `"use client"`; no SSR-critical code. API base URL is **hardcoded** to `http://localhost:8000/api` at [`web/src/app/lib/swr.ts:8`](web/src/app/lib/swr.ts:8). That one-liner needs to become `process.env.NEXT_PUBLIC_API_URL ?? "/api"` so the single-domain reverse-proxy layout works in prod.

### External dependencies
- PostgreSQL 17 (Alembic-managed; 6 migrations under [`alembic/versions/`](alembic/versions/)).
- Redis 5+ (ARQ queue).
- Playwright Chromium (per-worker browser).
- OpenAI API (required); DeepSeek, SerpApi, Adzuna, Reed, Coresignal, webhook (optional / feature-gated).

### Config plumbing to be aware of
- `configs/app.toml` is read via **relative path** in three places: [`settings.py:18`](src/vacancysoft/settings.py:18), [`worker/settings.py:21`](src/vacancysoft/worker/settings.py:21), and `intelligence/dossier.py`. The container MUST set `WORKDIR=/app` so the repo's `configs/` resolves correctly.
- `configs/app.toml` hardcodes `database_url = "postgresql://localhost/prospero"` — that needs to become env-driven or we have to template the file at image-build time.
- `REDIS_URL` is env-driven already (`src/vacancysoft/worker/settings.py:28`).
- Alembic reads the DB URL from `get_settings()`, not `alembic.ini` (`alembic/env.py:12-13`).
- `artifacts/raw/` is written to — needs a persistent volume or Azure Files mount.

### macOS-isms that must die in prod
`start.sh` uses `lsof`, `pkill`, `brew services`, `/opt/homebrew/opt/postgresql@17/...`, and `open`. All replaced by container entrypoints.

---

## Target architecture on Azure

```
  ┌──────────────────────────────────────────────────────────┐
  │ Azure Front Door / Application Gateway (TLS + WAF)       │
  │   └── Entra ID Easy Auth ("Authentication" built-in)     │
  └───────────────────┬──────────────────────────────────────┘
                      │ single hostname: prospero.<corp>.com
                      ▼
  ┌──────────────────────────────────────────────────────────┐
  │ Azure Container Apps Environment (VNet-integrated)       │
  │                                                          │
  │  ┌──────────────┐    ┌──────────────┐    ┌───────────┐   │
  │  │ web (Next.js)│    │ api (FastAPI)│    │ worker    │   │
  │  │  1 replica   │◄──▶│  1-3 replicas│    │ (ARQ)     │   │
  │  │  port 3000   │    │  port 8000   │    │ 1-N rep.  │   │
  │  │  `/` + `/*`  │    │  `/api/*`    │    │ no ingress│   │
  │  └──────────────┘    └──────┬───────┘    └─────┬─────┘   │
  │                             │                  │         │
  │                             ▼                  ▼         │
  │   ┌───────────────────┐   ┌───────────────────────┐      │
  │   │ Container Apps Job│   │ shared Azure Files    │      │
  │   │  "scraper-cron"   │   │  volume (artifacts/)  │      │
  │   │  EventTrigger     │   └───────────────────────┘      │
  │   │  hourly/daily     │                                  │
  │   └───────────────────┘                                  │
  └──────────────────────────────────────────────────────────┘
              │                          │
              ▼                          ▼
   ┌───────────────────────┐   ┌───────────────────────┐
   │ Azure DB for          │   │ Azure Cache for Redis │
   │ PostgreSQL Flexible   │   │ (Basic C0 or C1)      │
   │ Server (B1ms or GP)   │   │                       │
   └───────────────────────┘   └───────────────────────┘
                │
                ▼
       Azure Key Vault (OpenAI / SerpApi / Adzuna / Reed / Coresignal / webhook)
```

### Why these choices
- **Container Apps** over App Service: supports multi-container app bundles (api + worker + web as separate apps in one environment), native KEDA scale-to-zero for the worker, native Jobs feature for the scraper cron, and native Dapr if we later want it. Also has **built-in Entra ID auth** (Easy Auth) that solves the "internal team access" requirement with zero code changes.
- **Entra ID Easy Auth (recommended)** over Cloudflare Access: since you're already on Azure, Easy Auth on Container Apps is free, lives on the same ingress, and maps 1:1 to your corporate directory. Cloudflare Access is a fine fallback if the team isn't on Entra.
- **Single domain, API under `/api`**: the Container Apps ingress can route `/api/*` to the api app and everything else to the web app via path-based routing, which means CORS can be locked to the single origin and the frontend just fetches `/api/...` as a relative path.

---

## Step-by-step plan

### Phase 1 — Make the app container-ready (code + config changes)

Ordered by blast radius, smallest first.

#### 1a. Frontend: env-driven API base URL  *(file: `web/src/app/lib/swr.ts`)*
Change line 8 from a hardcoded constant to:
```ts
export const API = process.env.NEXT_PUBLIC_API_URL ?? "/api";
```
Rationale: in single-domain reverse-proxy mode the frontend fetches `/api/...` as a same-origin relative path. No other frontend file needs touching — every other fetch site uses `API`.

#### 1b. Backend: environment override for database URL  *(file: `src/vacancysoft/settings.py`)*
Currently `get_settings()` reads `database_url` only from `configs/app.toml`. Add an env override so the secret stays out of the image:
```python
database_url = os.getenv("DATABASE_URL") or app_cfg.get("database_url", "sqlite:///./.data/prospero.db")
```
Alembic will then automatically pick it up too because `alembic/env.py` calls `get_settings()`.

#### 1c. Backend: tighten CORS in prod  *(file: `src/vacancysoft/api/server.py:20-26`)*
Replace `allow_origins=["*"]` with an env-driven allow-list:
```python
allow_origins=os.getenv("CORS_ALLOWED_ORIGINS", "*").split(","),
```
In prod we set `CORS_ALLOWED_ORIGINS=https://prospero.<corp>.com` (or just `*` if everything is same-origin via the path proxy — Easy Auth strips cross-origin concerns anyway).

#### 1d. Backend: config file resolution  *(leave as-is for now)*
`configs/app.toml`, `configs/exporters.toml`, `configs/agency_exclusions.yaml`, `configs/seeds/` are all read by relative path. Solved by `WORKDIR /app` in the Dockerfile — no code change needed.

#### 1e. Remove the stale `database_url` from `configs/app.toml`
Point it at `postgresql://prospero:prospero@localhost/prospero` for local dev only; prod value comes from env. Document in `configs/app.toml` comment.

**Files touched in Phase 1**: 3 source files + 1 config. Net ~6 lines of code change. No schema or test changes.

---

### Phase 2 — Dockerfiles and compose

Create three Dockerfiles (one image per service, all from the same repo):

#### `Dockerfile.api`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY src ./src
COPY configs ./configs
COPY alembic ./alembic
COPY alembic.ini ./
EXPOSE 8000
# Migrations are run by a separate one-shot "migrator" container / job —
# NOT at api startup, to avoid races when multiple api replicas come up
# together. See docker-compose.prod.yml `migrate` service + the
# `job-migrate` Container Apps Job in the Bicep template.
CMD ["uvicorn", "vacancysoft.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### `Dockerfile.migrate`
Tiny one-shot image that reuses the same base layers as the api image but runs Alembic and exits.
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY src ./src
COPY configs ./configs
COPY alembic ./alembic
COPY alembic.ini ./
CMD ["alembic", "upgrade", "head"]
```
Invocation:
- **Local parity (`docker-compose.prod.yml`)**: a `migrate` service that runs to completion, and `api` + `worker` `depends_on: { migrate: { condition: service_completed_successfully } }`.
- **Azure**: a separate **Container Apps Job** `job-migrate` (manual / event-triggered). The deploy workflow runs `az containerapp job start --name job-migrate ...` and waits for completion *before* rolling out the new api/worker images. The api container no longer touches Alembic.

#### `Dockerfile.worker`
Same base but bundles Chromium (the user's explicit choice for browser strategy).
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY src ./src
COPY configs ./configs
# Chromium is pre-installed in the Playwright base image — no `playwright install` needed.
CMD ["arq", "vacancysoft.worker.settings.WorkerSettings"]
```
The Playwright base image is the cleanest way to get a known-good Chromium + all system libs; ~1.3 GB compressed. Don't try to bake Chromium into `python:3.12-slim` yourself, it's a rabbit hole.

#### `Dockerfile.web`
```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY web ./
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/public ./public
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./
EXPOSE 3000
CMD ["npm", "run", "start"]
```

#### `docker-compose.prod.yml`
For local dry-run before pushing to Azure. Include postgres, redis, api, worker, web, plus an `nginx` service acting as the single-domain reverse proxy (matches the Azure ingress routing so local parity is maintained).

**Deliverable**: `docker compose -f docker-compose.prod.yml up` reproduces the prod topology on a laptop.

---

### Phase 3 — Azure resources (IaC in Bicep)

One `infra/main.bicep` with parameters for env name + region. Resources:

1. **Resource group** — `rg-prospero-<env>`.
2. **Log Analytics workspace** + **Application Insights** — centralised logs/metrics for all apps.
3. **Azure Container Registry (Basic SKU)** — image hosting. Pushed via `az acr build` from each Dockerfile.
4. **Azure Key Vault** — stash the secrets that are currently in `.env` (OPENAI_API_KEY, DEEPSEEK_API_KEY, SERPAPI_KEY, ADZUNA_*, REED_*, CORESIGNAL_*, WEBHOOK_URL). Container Apps consumes them via managed-identity + `secretRef` in the app definition.
5. **Azure Database for PostgreSQL Flexible Server** — start with B1ms (~£10/mo), 1 core, 2 GB RAM. Enable public endpoint only from the Container Apps environment's outbound IP, or better: private endpoint into the app VNet.
6. **Azure Cache for Redis** — C0 Basic (250 MB, ~£12/mo) is plenty for an ARQ queue of thousands of jobs; bump to C1 if the queue depth grows.
7. **Container Apps Environment** — workload profile `Consumption` (cheapest) with VNet integration so the DB+Redis traffic stays private.
8. **Three Container Apps**:
   - `ca-api` — 1–3 replicas, 0.5 vCPU / 1 GB, HTTP ingress on `/api/*`, Entra ID Easy Auth enabled.
   - `ca-web` — 1–2 replicas, 0.25 vCPU / 0.5 GB, HTTP ingress on `/*` (lower priority than api so `/api/*` wins), Entra ID Easy Auth enabled at the same ingress.
   - `ca-worker` — 1–N replicas, 1 vCPU / 2 GB (Chromium needs the headroom), **no ingress**, KEDA scaler watching Redis list length so it scales 0→N as the ARQ queue fills.
9. **Container Apps Job `scrape-cron`** — CronJob trigger `0 * * * *` (or whatever cadence you pick), runs a one-shot command that either (a) calls the API to enqueue `scrape_source` for every active source, or (b) runs `prospero pipeline discover --all` directly if we want to bypass the queue. Recommend (a) because it reuses the worker pool and surfaces failures in the same place.

### Ingress routing (single domain)

The Container Apps environment ingress supports path-based routing:
- `https://prospero.<corp>.com/api/*`  → `ca-api`
- `https://prospero.<corp>.com/*`      → `ca-web`

Easy Auth is attached at the ingress level — both paths require Entra ID sign-in. **This is the recommended auth choice** because it needs zero code changes and is free with Container Apps.

---

### Phase 4 — Deployment workflow

Add a GitHub Actions workflow `.github/workflows/deploy.yml`:

1. Trigger: push to `main` or manual `workflow_dispatch`.
2. Job matrix: build + push each of the **four** images to ACR via `az acr build` (api, migrate, worker, web). Serverless; no GHA runner builds.
3. **Run migrations first**: `az containerapp job start --name job-migrate --resource-group rg-prospero-<env>` and **wait for exit code 0** before any further step. If it fails, abort the deploy — the new api/worker would crash against a schema that doesn't yet match.
4. Deploy: `az containerapp update --image ...` for `ca-api`, `ca-worker`, and `ca-web` in parallel.
5. Smoke-test: hit `/api/stats` with a bearer token (or via `az containerapp exec curl`) and confirm 200 before declaring the deploy done.

The existing [`ci.yml`](.github/workflows/ci.yml) stays in place and is a prerequisite check on the PR that merges to `main`.

---

### Phase 5 — Data & secrets migration

1. Export local data (if the operator has any prod-like data in their local Postgres):
   - `pg_dump prospero > prospero.sql`.
2. Restore into Azure Postgres: `psql -h <server>.postgres.database.azure.com prospero < prospero.sql`.
3. Move each `.env` key into Key Vault (one secret per key). Reference from Container Apps via `secretRef`. **Do not copy `.env` into the image** — the `.gitignore` is correct, but the image must not contain it either.
4. Note: the local `.env` currently on disk has real API keys. Treat those as already-leaked once the repo is published anywhere. Rotate all of them (OpenAI, SerpApi, Adzuna, Reed, Coresignal, DeepSeek, webhook) before the first prod deploy.

---

### Phase 6 — Smoke test the deployed instance

Follow-the-pipeline test, in order:

1. Log in via Entra ID at `https://prospero.<corp>.com/` → dashboard loads.
2. `/sources` page shows seeded sources. Click "Scrape" on one → worker picks it up → source card updates.
3. `/leads` shows new leads after a scrape.
4. Click a lead → dossier generates → campaign preview renders.
5. Verify cron by watching the scraper-cron job logs in Log Analytics: it should fire on schedule and enqueue `scrape_source` for every active source.
6. `az containerapp logs show --name ca-api --follow` — look for "Warmed ledger cache" on startup (confirms the startup hook runs) and no `Redis not available` warnings.
7. Kill the worker replica mid-dossier; confirm the self-heal sweep on next worker startup re-enqueues the stuck `ReviewQueueItem` (`src/vacancysoft/worker/self_heal.py`).

---

## Open questions / follow-ups (not blocking initial deploy)

- **Artifacts volume**. `artifacts/raw/` is written by adapters for debugging. Probably fine as ephemeral per-replica storage for now, but if an operator needs to inspect a failed scrape, mount an Azure Files share to `/app/artifacts`. Flag as P4 after first deploy.
- **Frontend hardcoded user badge** ([`web/src/app/components/Sidebar.tsx:74-75`](web/src/app/components/Sidebar.tsx:74) "Antony B. / Pro Plan"). Pull from Entra ID claim via the `/.auth/me` endpoint that Easy Auth exposes. ~30 min UI change; not blocking.
- **Multi-tenant**. The MEMORY index notes "multi-user planned for later". This plan explicitly does not add tenant isolation — Easy Auth just checks the requester is on the corporate tenant. When multi-user lands, revisit.
- **Rate-limit / cost caps**. `configs/app.toml` has `job_timeout = 900` and 25 concurrent workers, which at worst case could burn ~£1-2 of OpenAI credit per minute. Recommend an Application Insights alert on spend rate as a follow-up.
- **Backups**. Azure Postgres Flexible Server auto-backups are 7 days by default. Extend to 35 days for the prod instance.
- **Scheduler cadence**. "Hourly for active sources, daily for the long tail" is a reasonable default — tune once traffic exists.

---

## Files the implementation will create or modify

**Modified**:
- `src/vacancysoft/settings.py` — env override for DATABASE_URL (~2 lines).
- `src/vacancysoft/api/server.py` — env-driven CORS (~2 lines).
- `web/src/app/lib/swr.ts` — env-driven API base (~1 line).
- `configs/app.toml` — reword the `database_url` line as a dev-only default.

**Created**:
- `Dockerfile.api`, `Dockerfile.migrate`, `Dockerfile.worker`, `Dockerfile.web` (four images).
- `docker-compose.prod.yml` — local parity, with `migrate` service that api/worker depend on via `service_completed_successfully`.
- `infra/main.bicep` + `infra/parameters.<env>.json` — Azure resources, including `job-migrate` Container Apps Job.
- `.github/workflows/deploy.yml` — build + push all four images, then run `job-migrate` and wait, then update api/worker/web.
- `docs/deployment.md` — runbook (env vars, first-deploy steps, rollback, migration procedure).

**Not touched**:
- Alembic migrations, adapters, pipeline code, tests, intelligence prompts. The codebase is already deployment-shaped — this plan is overwhelmingly packaging + infra, not refactoring.

---

## Verification

End-to-end check after the plan is executed:

1. `docker compose -f docker-compose.prod.yml up --build` locally → `curl http://localhost/api/stats` returns 200 → visiting `http://localhost/` in a browser shows the dashboard with live data from local Postgres.
2. `az acr build` produces three images, each < 2 GB (worker image dominated by Chromium).
3. `az deployment sub create --template-file infra/main.bicep ...` provisions the resource group in <15 min.
4. First deploy → `/api/stats` reachable behind Entra ID login → scrape cron fires on schedule → lead flow from discover→classify→dossier→campaign works end-to-end.
5. `pytest` still green; `cd web && npx tsc --noEmit` still green; existing CI unaffected.
