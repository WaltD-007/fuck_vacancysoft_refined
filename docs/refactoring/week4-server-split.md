# Week 4 — Splitting `src/vacancysoft/api/server.py`

One 2,240-line Python file is being broken into focused modules under
`src/vacancysoft/api/`. Same principles as the Week 3 frontend split:
one extraction per commit, zero behaviour change, verify at every step.

## Ground rules
- One module per commit.
- Zero behaviour change per step — every URL must still return the
  same JSON body it did before.
- Rollback: `git revert <sha>` per step; full week revert via
  `git revert <first-commit>..HEAD`.

## Verification per step
- `python3 -m pytest` — must stay at **359 passed, 1 pre-existing
  unrelated failure** (`test_classification.py::test_relevant[Pricing
  Actuary]`).
- Route list must equal the baseline captured below. Command:
  ```
  python3 -c "from vacancysoft.api.server import app;
  routes = sorted([(','.join(sorted(r.methods)) if hasattr(r,'methods') else '-', r.path)
                    for r in app.routes if hasattr(r,'path')]);
  [print(f'{m:<20} {p}') for m,p in routes]"
  ```
  Diff against `/tmp/baseline-routes.txt` (24 routes).
- `curl http://localhost:8000/api/sources | jq '.[0].employer_name'`
  should return the same employer name before and after each step.

## Starting baseline
- Branch: `chatgpt/adapter-updates`
- Starting HEAD: `29dda2f` (after the Week 3 frontend split)
- `server.py` size: **2,240 lines**
- `npx tsc --noEmit` (web): clean
- `pytest`: **359 passed**, 1 pre-existing unrelated failure
- API server: running on `:8000`, responds HTTP 200

## Baseline route list (24 total)

```
DELETE               /api/queue/{item_id}
DELETE               /api/sources/{source_id}
GET                  /api/countries
GET                  /api/dashboard
GET                  /api/leads/{item_id}/dossier
GET                  /api/queue
GET                  /api/sources
GET                  /api/sources/{source_id}/jobs
GET                  /api/stats
HEAD,GET             /docs
HEAD,GET             /docs/oauth2-redirect
HEAD,GET             /openapi.json
HEAD,GET             /redoc
POST                 /api/agency
POST                 /api/leads/{item_id}/campaign
POST                 /api/leads/{item_id}/dossier
POST                 /api/queue
POST                 /api/queue/{item_id}/send
POST                 /api/sources
POST                 /api/sources/add-company/confirm
POST                 /api/sources/add-company/search
POST                 /api/sources/detect
POST                 /api/sources/{source_id}/diagnose
POST                 /api/sources/{source_id}/scrape
```

## Planned extractions (in execution order)

Order comes from the structural exploration map: models → helpers →
routes. Each step's imports depend on the previous step having landed
(e.g. routers import from `ledger.py` and `schemas.py`).

| # | File | Contents | Endpoint count |
|---|---|---|---|
| 1 | `api/schemas.py` | Every Pydantic `BaseModel` (request / response) | 0 (data only) |
| 2 | `api/ledger.py` | `_build_source_card_ledger`, `_get_cached_ledger`, `_extract_employer_from_payload`, `_category_counts`, `_core_market_total`, `clear_ledger_caches` (new), cache dicts, `_CORE_MARKETS`, `_CATEGORY_LABELS`, `_AGGREGATOR_ADAPTERS` | 0 (library only) |
| 3 | `api/routes/leads.py` | `/api/stats`, `/api/dashboard`, `/api/countries`, `/api/queue*` + `_scrape_and_generate_dossier` background task | 7 |
| 4 | `api/routes/sources.py` | `/api/sources`, `/api/sources/{id}/jobs`, `/api/sources/detect`, `POST /api/sources`, `/api/sources/{id}/scrape`, `/api/sources/{id}/diagnose`, `DELETE /api/sources/{id}` | 7 |
| 5 | `api/routes/add_company.py` | `/api/sources/add-company/search`, `/api/sources/add-company/confirm` + Coresignal helpers | 2 |
| 6 | `api/routes/campaigns.py` | `/api/leads/{id}/dossier`, `/api/leads/{id}/campaign`, `/api/agency` | 4 |
| 7 | `api/server.py` (slim) | FastAPI app, CORS, startup / shutdown Redis hooks, router includes | — |

20 endpoint handlers total (24 routes once `/docs`, `/openapi.json`,
`/redoc`, `/docs/oauth2-redirect` are counted).

---

## Step 1 — `schemas.py`

Extracted every `class X(BaseModel):` from `api/server.py` into
`src/vacancysoft/api/schemas.py` — fourteen models total:

`SourceOut`, `DetectRequest`, `DetectResponse`, `AddSourceRequest`,
`AddSourceResponse`, `AddCompanyRequest`, `AddCompanyCandidate`,
`AddCompanyResponse`, `StatsOut`, `ScoredJobOut`, `QueueRequest`,
`ScrapeResponse`, `MarkAgencyRequest`, `MarkAgencyResponse`.

`server.py` now imports them all from `schemas` and drops the
`from pydantic import BaseModel` import at the module level. Handlers
are unchanged; response models still validate exactly as before.
`ScrapeResponse` was defined but never referenced in a handler — kept
in `schemas.py` for future use, removed from `server.py`'s imports.

Verification:
- `python3 -c "from vacancysoft.api.server import app; ..."` — 24
  routes, diff vs `/tmp/baseline-routes.txt` empty.
- `pytest` — 359 passed (same pre-existing single failure).
- `curl http://localhost:8000/api/sources | jq 'length'` — 5,285 (same
  as baseline). First employer_name: "Goldman Sachs".

File sizes after step:
- `api/server.py`: 2,240 → 2,114 lines
- `api/schemas.py`: 151 lines (new)

Rollback: `git revert <sha-of-step-1>`.

## Step 2 — `ledger.py`

_Pending._

## Step 3 — `routes/leads.py`

_Pending._

## Step 4 — `routes/sources.py`

_Pending._

## Step 5 — `routes/add_company.py`

_Pending._

## Step 6 — `routes/campaigns.py`

_Pending._

## Step 7 — slim `server.py`

_Pending._

## Final state

_Pending._
