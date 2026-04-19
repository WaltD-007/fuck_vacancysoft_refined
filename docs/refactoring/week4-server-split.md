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

Source-card ledger construction and associated caches move into
`src/vacancysoft/api/ledger.py`:

- Constants: `_CORE_MARKETS`, `_CATEGORY_LABELS`, `_AGGREGATOR_ADAPTERS`
- Caches: `_sources_cache`, `_ledger_cache`, `_SOURCES_CACHE_TTL`
- Helpers: `_category_counts`, `_core_market_total`,
  `_extract_employer_from_payload`, `_build_source_card_ledger`,
  `_get_cached_ledger`
- New `clear_ledger_caches()` — the single public cache-invalidation
  point. The two-liner `_sources_cache.clear(); _ledger_cache.clear()`
  inside the add-company confirm handler at `server.py:849-850` is
  replaced with `clear_ledger_caches()`, so future routers can depend
  on this stable API instead of the two dict names.

`server.py` now imports all of the above from `ledger`. The
`from __future__ import annotations` is no longer strictly needed but
is kept for consistency with the rest of the module.

`tests/test_source_card_ledger_empty.py` updated to import
`_build_source_card_ledger` from `vacancysoft.api.ledger` instead of
`vacancysoft.api.server` — closer to the real home; less indirection.

Verification:
- route list vs baseline → identical (24 routes)
- `pytest` → 359 passed, same pre-existing unrelated failure
- `curl /api/sources | jq 'length'` → 5,285, first: Goldman Sachs

File sizes:
- `api/server.py`: 2,114 → 1,731 lines
- `api/ledger.py`: 430 lines (new)

Rollback: `git revert <sha-of-step-2>`.

## Step 3 — `routes/leads.py`

First router extracted. Seven endpoints move into
`src/vacancysoft/api/routes/leads.py` via an `APIRouter`:

  GET    /api/stats                    `get_stats`
  GET    /api/dashboard                `get_dashboard`
  GET    /api/countries                `list_countries`
  POST   /api/queue                    `queue_campaign`
  GET    /api/queue                    `list_queue`
  POST   /api/queue/{item_id}/send     `send_to_campaign`
  DELETE /api/queue/{item_id}          `remove_from_queue`

Plus the `_scrape_and_generate_dossier` background task (~100 lines)
and the `_PLAYWRIGHT_SCRAPER_URL` constant it depends on — both move
with `queue_campaign` since they're only used as its in-process
fallback when Redis is unavailable.

`queue_campaign` now receives `request: Request` as a FastAPI
parameter and accesses the Redis pool via `request.app.state.redis`
instead of closing over the module-level `app`. Behaviour identical.

Also created:
- `src/vacancysoft/api/routes/__init__.py` (package marker)

`server.py` now:
- imports `leads as leads_routes` from `api.routes`
- calls `app.include_router(leads_routes.router)` right after the CORS
  middleware wiring
- drops `_category_counts`, `QueueRequest`, `StatsOut` from its import
  block — all now used only by leads.py

Verification:
- route list vs baseline → identical (24 routes)
- `pytest` → 359 passed, same pre-existing unrelated failure
- `GET /api/stats` → `total_scored=46632 total_jobs=140076`
- `GET /api/countries` → 12 entries, top = USA (20,949)

File sizes:
- `api/server.py`: 1,731 → 1,177 lines
- `api/routes/leads.py`: 585 lines (new)

Rollback: `git revert <sha-of-step-3>`.

## Step 4 — `routes/sources.py`

Seven more endpoints move out of `server.py` into
`src/vacancysoft/api/routes/sources.py`:

  GET    /api/sources
  GET    /api/sources/{source_id}/jobs
  POST   /api/sources/detect
  POST   /api/sources
  POST   /api/sources/{source_id}/scrape
  POST   /api/sources/{source_id}/diagnose
  DELETE /api/sources/{source_id}

Plus the three sources-specific constants: `_slugify()`, `ADAPTER_MAP`,
`_API_ONLY_ADAPTERS`. The inline `_PLATFORM_MARKERS` map inside
`diagnose_source` stays where it is (only that handler uses it).

`scrape_source_endpoint` and `diagnose_source` now take `request:
Request` and access the Redis pool via `request.app.state.redis`
instead of closing over the module-level `app`.

`server.py` imports dropped as their last consumers moved out:
`asyncio`, `hashlib`, `urllib.parse.urlparse`, `fastapi.responses.JSONResponse`,
`bindparam`, `source_detector.detect_platform`, `_CATEGORY_LABELS`,
`_SOURCES_CACHE_TTL`, `_build_source_card_ledger`, `_get_cached_ledger`,
`_ledger_cache`, `_sources_cache`, `AddSourceRequest`, `AddSourceResponse`,
`DetectRequest`, `DetectResponse`, `ScoredJobOut`, `SourceOut`,
`PLATFORM_REGISTRY`.

Verification:
- route list vs baseline → identical (24 routes)
- `pytest` → 359 passed, same pre-existing unrelated failure
- `GET /api/sources` → 5,285 rows, Goldman Sachs first

File sizes:
- `api/server.py`: 1,177 → 739 lines
- `api/routes/sources.py`: 468 lines (new)

Rollback: `git revert <sha-of-step-4>`.

## Step 5 — `routes/add_company.py`

_Pending._

## Step 6 — `routes/campaigns.py`

_Pending._

## Step 7 — slim `server.py`

_Pending._

## Final state

_Pending._
