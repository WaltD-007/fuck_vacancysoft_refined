# Prospero — dependency inventory

**Purpose**: single reference for every external thing Prospero depends on.
Useful for deployment planning, secret rotation, tenant-admin requests,
provisioning new environments, or answering "what happens if X is down?".

**Maintenance**: regenerate the Python + Node sections whenever `pyproject.toml`
or `web/package.json` changes (the commands at the bottom of this document
extract them). The other sections need manual maintenance when config files,
new adapters, or new environment variables are added.

**See also**: [deployment_plan.md](deployment_plan.md) for what these
dependencies look like in prod; [launch_plan.md](launch_plan.md) for what's
still blocked on configuring them.

---

## 1. Runtime platform

| Dependency | Version | Source | Required for |
|---|---|---|---|
| Python | ≥ 3.12 | [pyproject.toml](../pyproject.toml) `requires-python` | Everything backend |
| Node.js | 20+ (implied by Next.js 16) | [web/package.json](../web/package.json) | Frontend dev + build |
| PostgreSQL | 17 (hardcoded in start.sh for local dev; matches Azure Postgres Flexible Server prod target) | [start.sh](../start.sh) | Primary database |
| Redis | ≥ 5.0 | [pyproject.toml](../pyproject.toml) `redis>=5.0,<6` | ARQ job queue |
| Chromium | via Playwright ≥ 1.52 | [pyproject.toml](../pyproject.toml) `playwright>=1.52,<2` | Browser-based scraper adapters |

---

## 2. Python packages (20 runtime + 4 dev)

### Runtime — installed by `pip install -e .`

| Package | Constraint | Role |
|---|---|---|
| pydantic | `>=2.8,<3` | API + schema data models |
| sqlalchemy | `>=2.0,<3` | ORM |
| alembic | `>=1.13,<2` | DB migrations |
| typer | `>=0.12,<1` | `prospero` CLI framework |
| httpx | `>=0.27,<1` | HTTP client for all external APIs |
| playwright | `>=1.52,<2` | Browser automation |
| structlog | `>=24.1,<25` | Structured logging |
| rapidfuzz | `>=3.9,<4` | Title similarity / dedupe |
| python-dateutil | `>=2.9,<3` | Date parsing from scraper payloads |
| pyyaml | `>=6.0,<7` | Config file loading |
| tenacity | `>=9.0,<10` | Retry logic on external calls |
| openpyxl | `>=3.1,<4` | Taxonomy xlsx read/write + Excel exports |
| rich | `>=13.0,<15` | Pretty terminal output |
| openai | `>=1.0,<2` | GPT-5* API client (dossier + campaign) |
| psycopg2-binary | `>=2.9,<3` | Postgres driver |
| arq | `>=0.26,<1` | Redis-backed async job queue |
| redis | `>=5.0,<6` | Redis client used by ARQ |
| python-dotenv | `>=1.0,<2` | `.env` loading in CLI + worker |
| fastapi | `>=0.115,<1` | Web API framework |
| uvicorn[standard] | `>=0.30,<1` | ASGI server for FastAPI |

### Dev-only — `pip install -e ".[dev]"`

| Package | Constraint | Role |
|---|---|---|
| pytest | `>=8.3,<9` | Test runner |
| pytest-asyncio | `>=0.23,<1` | Async test support |
| ruff | `>=0.6,<1` | Linter (CI `continue-on-error: true` until rule set agreed) |
| mypy | `>=1.11,<2` | Type checker (not currently wired into CI) |

---

## 3. Node packages (4 runtime + 8 dev)

### Runtime (`web/`)

| Package | Version | Role |
|---|---|---|
| next | 16.2.3 | Frontend framework |
| react | 19.2.4 | UI runtime |
| react-dom | 19.2.4 | React renderer |
| swr | ^2.4.1 | Data fetching / caching on the frontend |

### Dev

| Package | Role |
|---|---|
| typescript ^5 | Type checking |
| eslint ^9 + eslint-config-next 16.2.3 | Linting |
| tailwindcss ^4 + @tailwindcss/postcss ^4 | Styling |
| @types/node ^20, @types/react ^19, @types/react-dom ^19 | Type stubs |

---

## 4. External SaaS / APIs the app calls

### 4a. Intelligence layer (LLM + search)

| Service | Purpose | Auth env var | Required? |
|---|---|---|---|
| **OpenAI** (`api.openai.com`) | Dossier generation, campaign generation, HM search (default path) | `OPENAI_API_KEY` | **Yes** — core |
| DeepSeek (`api.deepseek.com`) | Alternative LLM provider | `DEEPSEEK_API_KEY` | Optional — only if `use_deepseek_for_dossier` or `use_deepseek_for_campaign` in `configs/app.toml` is true (both default `false`) |
| SerpApi (`serpapi.com`) | Alternative hiring-manager search via Google LinkedIn scraping; powers the Google Jobs adapter | `SERPAPI_KEY` | Optional — when `use_serpapi_hm_search=true` (default `true`). Falls back to OpenAI web-search if missing |

### 4b. Microsoft Graph (pre-launch — see [launch_plan.md](launch_plan.md) item 1A.2)

| Service | Purpose | Auth | Status |
|---|---|---|---|
| Microsoft Graph `/users/{user}/sendMail` | Campaign send via user's Outlook mailbox | `Mail.Send` application permission | Blocked on tenant admin approval |
| Microsoft Graph `/users/{user}/messages` | Reply + bounce polling | `Mail.Read` application permission | Blocked on tenant admin approval |
| Entra ID (Microsoft Entra) | Easy Auth on Container Apps ingress, plus app registrations for Graph | App registrations (separate ones for auth vs Graph) | Not yet created |

### 4c. Aggregator job feeds

| Service | Auth env var | Adapter module | Purpose |
|---|---|---|---|
| Adzuna (`api.adzuna.com`) | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | [adapters/adzuna.py](../src/vacancysoft/adapters/adzuna.py) | UK + US aggregated listings |
| Reed (`www.reed.co.uk`) | `REED_API_KEY` | [adapters/reed.py](../src/vacancysoft/adapters/reed.py) | UK aggregator |
| Coresignal (`api.coresignal.com`) | `CORESIGNAL_API_KEY` | [adapters/coresignal.py](../src/vacancysoft/adapters/coresignal.py) | Multi-source deep archive |
| Google Jobs (via SerpApi) | `SERPAPI_KEY` (shared with HM search) | [adapters/google_jobs.py](../src/vacancysoft/adapters/google_jobs.py) | Google-indexed listings |
| eFinancialCareers (`efinancialcareers.co.uk`) | Public scrape, no key | [adapters/efinancialcareers.py](../src/vacancysoft/adapters/efinancialcareers.py) | Finance-specific listings |

### 4d. Direct ATS / job-board APIs

[src/vacancysoft/adapters/](../src/vacancysoft/adapters/) has 24 adapter modules targeting ATSes and job boards directly. Most are per-tenant — each employer has their own subdomain — so there isn't a single vendor endpoint to cite. Families in the adapter layer:

- **API-based (no browser)**: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Workday (per-tenant `*.myworkdayjobs.com`), Personio, Pinpoint, Phenom, JazzHR, HiBob, Recruitee, Teamtailor, BambooHR, ClearCompany, iCIMS, Infor, SuccessFactors, Oracle Cloud Recruiting, Salesforce Recruit, SelectMinds, Taleo, Eightfold, SilkRoad, ADP, Beamery
- **Browser-based (Playwright)**: `generic_browser.py`, some Workday flows, Cloudflare-gated sites

**Risk note**: browser-based adapters may be blocked from Azure IP ranges in production. See [launch_plan.md](launch_plan.md) risk assessment.

### 4e. Miscellaneous external dependencies

| Service | Purpose | Status |
|---|---|---|
| `playwright-runner.bluecliff-1ceb6690.uksouth.azurecontainerapps.io` | Legacy Playwright runner from a previous deployment; used by the parked `url_scrape.py` paste-URL feature | Pre-existing infra; decision pending whether to replace or depend on |
| `docs.google.com` | Sourced at runtime by `legacy_mapping.py` to sync taxonomy from a Google Sheet | Optional — only if `live_source.enabled=true` in `configs/legacy_routing.yaml` |

---

## 5. Configuration files (data the app needs to run)

Every file below lives in [configs/](../configs/) unless stated. All load via relative paths from CWD, which is why the container must `WORKDIR /app`.

| File | Role | Writable at runtime? | Canonical source |
|---|---|---|---|
| `app.toml` | All runtime settings (DB URL, worker, intelligence model routing, cost knobs) | No | This file |
| `agency_exclusions.yaml` | Recruitment agency exclusion list | **Yes** — via `prospero agency add` CLI or `POST /api/agency` (DB cascade only in prod) | `AGENCY_EXCLUSIONS_PATH` env var points here in prod (persistent volume) |
| `exporters.toml` | Export profile definitions for `prospero export` | No | This file |
| `legacy_routing.yaml` | Category + sub-specialism taxonomy for legacy export path | No (optionally synced from Google Sheets at runtime) | This file |
| `location_rules.yaml` | Country normalisation + allowed countries | No | This file |
| `scoring.toml` | Scoring weights for export eligibility | No | This file |
| `seeds/employers.yaml` | Seed list of employers on DB init | No | This file |
| `artifacts/taxonomy/*.xlsx` | Human-readable mirror of Python `_TAXONOMY_RULES` | Regenerated from Python; gitignored | [src/vacancysoft/classifiers/taxonomy.py](../src/vacancysoft/classifiers/taxonomy.py) — Python is authoritative |

---

## 6. Environment variables

All loaded from `.env` via `python-dotenv` in the CLI, API, and worker entrypoints.

### 6a. Required for core functionality

| Key | Purpose | Behaviour if missing |
|---|---|---|
| `OPENAI_API_KEY` | Dossier + campaign LLM calls | Dossier generation fails; queue items stall |
| `DATABASE_URL` | Postgres connection | Falls back to `configs/app.toml` → `postgresql://localhost/prospero` |
| `REDIS_URL` | ARQ queue | Falls back to `configs/app.toml` → `redis://localhost:6379` |

### 6b. Required for full scraping coverage

| Key | Purpose | Behaviour if missing |
|---|---|---|
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Adzuna adapter | Adzuna scraper silently returns zero |
| `REED_API_KEY` | Reed adapter | Same for Reed |
| `CORESIGNAL_API_KEY` | Coresignal adapter | Same for Coresignal |
| `SERPAPI_KEY` | SerpApi HM search + Google Jobs adapter | Google Jobs adapter returns zero; HM search falls back to OpenAI web-search path |

### 6c. Optional / flag-gated

| Key | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | Only consulted if `use_deepseek_for_*=true` in `configs/app.toml` |

### 6d. Deployment-time (added by option-A refactor)

| Key | Purpose |
|---|---|
| `CORS_ALLOWED_ORIGINS` | Comma-separated list; API restricts CORS to these origins in prod (local default: `*`) |
| `AGENCY_EXCLUSIONS_PATH` | Override location for the runtime YAML. In prod should point at a persistent volume (Azure Files) so the file survives container restarts. Default: `configs/agency_exclusions.yaml` |
| `NEXT_PUBLIC_API_URL` | Build-time frontend override. Default: `/api` (single-domain reverse-proxy mode) |

### 6e. Microsoft Graph (pre-launch, not yet provisioned)

| Key | Purpose |
|---|---|
| `GRAPH_TENANT_ID` | Entra tenant ID for the Graph app registration |
| `GRAPH_CLIENT_ID` | Graph app reg client ID |
| `GRAPH_CLIENT_SECRET` | Client credentials flow secret |

---

## 7. Azure resources (production target)

Not yet provisioned. Full Bicep stubs in [infra/main.bicep](../infra/main.bicep); full topology in [deployment_plan.md](deployment_plan.md). Summary:

- Resource Group × 1 per environment (`rg-prospero-stage`, `rg-prospero-prod`)
- Azure Container Apps Environment × 1
- Container Apps × 3: `ca-api`, `ca-worker`, `ca-web`
- Container Apps Jobs × 2: `job-migrate`, `job-scrape-cron`
- Azure Container Registry (Basic SKU, shared across stage + prod)
- Azure Database for PostgreSQL Flexible Server × 1 per env
- Azure Cache for Redis (Basic C0) × 1 per env
- Azure Key Vault × 1 per env (secrets injected as `secretRef`)
- Log Analytics Workspace + Application Insights × 1 shared
- VNet + subnets + private endpoints for Postgres and Redis
- Azure Files share (for `AGENCY_EXCLUSIONS_PATH` persistence)
- Entra ID app registration × 2 per env: one for Easy Auth (ingress), one for Graph (email send)

---

## 8. Build / dev tooling

| Tool | Used for | Required? |
|---|---|---|
| Homebrew | PostgreSQL + Redis install on Mac dev | Dev only |
| lsof, pkill | `start.sh` process management | Dev only |
| GitHub CLI (`gh`) | CI interaction, deploy workflow | Dev + CI |
| Docker | Local reproduction of prod topology via `docker-compose.prod.yml` | Pre-deploy |
| Azure CLI (`az`) | Deploy workflow invocations | Pre-deploy |
| `pg_dump` + `psql` | Backup/restore during migrations and local taxonomy rollbacks | Dev + ops |

---

## 9. Frontend → backend coupling

The Next.js frontend in [web/](../web/) has no server-side fetches (every page is `"use client"`). Every data flow goes through the API. Endpoints consumed per page:

| Page | API endpoints |
|---|---|
| `/` (Dashboard) | `/api/dashboard`, `/api/queue` (GET + POST), `/api/agency` (POST) |
| `/leads` | `/api/queue`, `/api/leads/paste`, `/api/leads/{id}/dossier`, `/api/leads/{id}/campaign`, `/api/queue/{id}` (DELETE) |
| `/sources` | `/api/sources`, `/api/stats`, `/api/countries`, `/api/sources/detect`, `/api/sources` (POST), `/api/sources/{id}/scrape`, `/api/sources/{id}/diagnose`, `/api/sources/{id}/jobs`, `/api/sources/{id}` (DELETE) |
| `/builder` | `/api/queue`, `/api/leads/{leadId}/campaign` |
| `/campaigns` | None yet — page is a hardcoded mock behind `FEATURES.campaignsManager=false` |

---

## 10. Critical-path summary

Three categories are **non-optional for launch**:

1. **Platform**: Python 3.12, Postgres 17, Redis 5+, Chromium, Node 20
2. **LLM**: OpenAI API key (DeepSeek + SerpApi are optional behind flags)
3. **Scraping feeds**: Adzuna + Reed + Coresignal API keys (or lose ~30% of lead volume)
4. **Email send** (launch-blocker): Microsoft Graph `Mail.Send` + `Mail.Read` — gated on tenant admin approval

Everything else degrades gracefully:

- No DEEPSEEK_API_KEY → DeepSeek toggles forced off, OpenAI fills the role
- No SERPAPI_KEY → HM search silently falls back to OpenAI
- Per-adapter failures → self-heal + other adapters still run
- No Graph access at launch → email send disabled (the app becomes read-only)

---

## 11. Regenerating this document

The Python + Node dependency sections are extractable. When either `pyproject.toml` or `web/package.json` changes, rerun:

```bash
# Python runtime deps
python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); [print(f'| {x.split(\">=\")[0].split(\"[\")[0]} | `{x}` | |') for x in d['project']['dependencies']]"

# Node deps (runtime + dev)
python3 -c "import json; d=json.load(open('web/package.json')); [print(f'| {k} | {v} | |') for k,v in d['dependencies'].items()]; print('---'); [print(f'| {k} | {v} | |') for k,v in d['devDependencies'].items()]"
```

The external URL list (section 4) can be re-derived by grepping:

```bash
grep -rE "https://[a-z0-9.-]+\.(com|io|ai|net|co|uk|org)" src/vacancysoft/ \
  | grep -oE "https://[a-z0-9.-]+\.(com|io|ai|net|co|uk|org)" \
  | sort -u
```

Sections 5 (configs), 6 (env vars), 7 (Azure), 8 (tooling), and 9 (frontend routes)
need **manual** updates when those parts of the system change.
