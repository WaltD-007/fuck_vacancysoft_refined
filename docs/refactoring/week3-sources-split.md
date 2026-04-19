# Week 3 — Splitting `web/src/app/sources/page.tsx`

One monolithic 1,330-line client component is being broken into smaller
components under `web/src/app/sources/components/`. This log records every
step so any individual change can be rolled back cleanly via `git revert`.

## Ground rules
- One component per commit.
- Zero behaviour change per step — `npx tsc --noEmit` must stay clean, and
  the page must still render and behave identically.
- Shared types move into `web/src/app/sources/types.ts` the first time
  more than one component needs them.

## Verification per step
- `cd web && npx tsc --noEmit` — must pass.
- `cd web && npm run lint` — must pass (if it was passing before the step).
- Manual smoke: load the sources page, confirm buckets, filters, modal,
  card expand, and scrape-now all still work.

## Rollback
Each step is its own commit. To undo a single step:
```
git revert <commit-sha>
```
The commit messages reference this log entry so the mapping is explicit.

---

## Starting baseline
- Branch: `chatgpt/adapter-updates`
- Starting HEAD: `ad730c6` (before the refactor)
- `page.tsx` size: 1,330 lines
- `npx tsc --noEmit`: clean

## Planned extractions (in execution order)

Order comes from the structural exploration report: simpler / fewer
dependencies first. The nominal feature order would be SourceCard →
AddSourceModal → StatTile → SourceJobsDrawer → SourceFilters, but
SourceCard embeds the drawer inline, so the drawer must come first.

| # | Component | Lines (approx, in starting file) | Notes |
|---|---|---|---|
| 0 | `types.ts` | N/A (extraction of types at lines 8–57 + `AGGREGATOR_LABELS` at 544–550, `categoryColors` at 579–587) | Foundation — no UI change |
| 1 | `SourceJobsDrawer` | 1218–1293 | Expanded per-card job list, currently embedded in the card IIFE |
| 2 | `SourceFilters` | 607–625 + 1050–1062 | Country + employment dropdowns + filter-label / clear |
| 3 | `SourceCard` | 1072–1295 + helper fns 466–476 | Composes `SourceJobsDrawer` as a child |
| 4 | `AddCompanyModal` | 651–784 + handlers 163–245 | Multi-phase wizard — search → confirm → scrape |
| 5 | `StatsSection` | 882–1035 | Stat tiles + category/adapter/aggregator chips |

Each entry below gets filled in as the step is executed.

---

## Step 0 — Shared `types.ts`

Created `web/src/app/sources/types.ts` with every cross-component type
and constant. Swapped `page.tsx` to import from it instead of inlining.

Moved:
- `Source`, `Stats`, `ScoredJob`, `DetectResult` — were inline types at
  the top of `page.tsx`
- `AddCompanyCandidate` — was nested inside the `SourcesPage()` body
- `SourceView` (new union alias) — the inline string-literal type
  `"leads" | "no_jobs" | "not_relevant" | "broken" | "all"` was repeated
  and now has a name
- `AGGREGATOR_LABELS` — was a `const` defined mid-render at ~line 544
- `CATEGORY_COLORS` — was `categoryColors` defined mid-render at ~line 579

Verification:
- `cd web && npx tsc --noEmit` → clean
- `curl http://localhost:3000/sources` → HTTP 200 (user's dev server
  picked up the change via HMR)

Rollback: `git revert <sha-of-step-0>`.

## Step 1 — `SourceJobsDrawer`

_Pending._

## Step 2 — `SourceFilters`

_Pending._

## Step 3 — `SourceCard`

_Pending._

## Step 4 — `AddCompanyModal`

_Pending._

## Step 5 — `StatsSection`

_Pending._

## Final state

_Pending._
