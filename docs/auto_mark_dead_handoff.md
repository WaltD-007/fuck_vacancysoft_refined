# Auto-mark-dead — handoff & operator guide

**Status**: Shipped to `main` 2026-04-29. Feature flag default `false` — no behaviour change until you flip it.
**Plan reference**: `~/.claude/plans/auto-mark-dead-jobs.md` (design doc)
**Commit**: `1652586` (pushed to `origin/main`)

---

## TL;DR

- Schema migration `0016_add_deleted_at_source_at` ran on your local DB during build; no production migration to run if main IS your local DB.
- Feature flag `[pipeline] auto_mark_dead_enabled = false` in `configs/app.toml` — flip to `true` when ready.
- 7 API adapters opted in (Workday, Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Teamtailor).
- 5 aggregators explicitly opted out (Adzuna, Reed, CoreSignal, Google Jobs, eFinancialcareers).
- Browser adapters (iCIMS, Oracle Cloud, SuccessFactors, etc.) stay at default `False`. Opt them in individually after monitoring.
- Re-discovery resets the flag automatically; no operator action needed when an employer re-posts a previously-killed job.
- "Recently deleted" panel on the Dashboard shows the last 7 days of sweep deletions with one-click Undelete.

---

## Rollback (if anything goes wrong)

### Cheapest rollback: flip the flag

```toml
# configs/app.toml
[pipeline]
auto_mark_dead_enabled = false  # ← back to false
```

Restart the worker / API to pick it up. No code revert needed. Already-marked-dead rows stay marked but no new ones land.

### Full code rollback

```bash
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined"
git revert 1652586
git push origin main
```

The revert is clean (single squash-style commit, no dependents).

### DB rollback

If you need to remove the schema change too:

```bash
alembic downgrade 0015_add_archived_at
```

Drops the `deleted_at_source_at` column and its index. Any rows previously flagged stay `is_deleted_at_source=True` (the existing column is unchanged) — if you also want to clear those, run:

```sql
UPDATE raw_jobs SET is_deleted_at_source = FALSE WHERE deleted_at_source_at IS NOT NULL;
```

before downgrading. Backup of the live DB pre-change is at `.data/backups/prospero-pre-auto-mark-dead-2026-04-29-2308.sql`.

---

## How to turn it on

### Step 1: enable the flag

Edit `configs/app.toml`:
```toml
[pipeline]
auto_mark_dead_enabled = true
auto_mark_dead_threshold = 0.75
```

Restart `prospero` worker + API.

### Step 2: run a scrape

```bash
prospero pipeline run --adapter workday
```

### Step 3: check what got swept

```sql
-- recent runs that swept
SELECT id, source_id, started_at, records_seen,
       diagnostics_blob->>'marked_dead' AS marked_dead,
       diagnostics_blob->>'sweep_skipped' AS skipped,
       diagnostics_blob->>'sweep_skip_reason' AS skip_reason
FROM source_runs
WHERE finished_at > NOW() - INTERVAL '1 day'
  AND status = 'success'
ORDER BY finished_at DESC
LIMIT 30;
```

Or open the Dashboard — the "Recently deleted" panel at the bottom shows the last 7 days. Click to expand.

### Step 4: spot-check 20 random sweeps

For the first few days, click into 20 random "Recently deleted" rows and verify the original URL is genuinely 404 / "no longer accepting applications". If false-positive rate is meaningful (>1%), flip the flag back to false and dig into the offending source.

---

## What got built

### DB

Migration `0016_add_deleted_at_source_at`:
- New column `raw_jobs.deleted_at_source_at TIMESTAMP NULL`
- New index `ix_raw_jobs_deleted_at_source_at` on `(is_deleted_at_source, deleted_at_source_at)`

### Code (~600 LOC)

| File | What |
|---|---|
| `alembic/versions/0016_add_deleted_at_source_at.py` | Schema migration (up+down, tested) |
| `src/vacancysoft/db/models.py` | `RawJob.deleted_at_source_at` mapped column |
| `src/vacancysoft/adapters/base.py` | New `AdapterCapabilities.complete_coverage_per_run` (default `False`) |
| Adapter files (12) | Opt-in `True` for Workday, Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Teamtailor; explicit `False` for Adzuna, Reed, CoreSignal, Google Jobs, eFinancialcareers |
| `src/vacancysoft/pipelines/persistence.py` | `finalise_source_run` now calls `_maybe_sweep_dead_jobs`; `upsert_raw_job` now resets the flag on rediscovery |
| `configs/app.toml` | New `[pipeline]` section with the feature flag + threshold |
| `src/vacancysoft/api/ledger.py` | All RawJob queries filter `is_deleted_at_source=False` |
| `src/vacancysoft/api/routes/leads.py` | Stats / dashboard / countries queries filter dead RawJobs; new `GET /api/leads/recently-deleted` and `POST /api/leads/{enriched_job_id}/undelete` endpoints |
| `src/vacancysoft/cli/app.py` | New `prospero db undelete-job <raw_job_id>` |
| `web/src/app/page.tsx` | Default-collapsed "Recently deleted" panel on Dashboard |
| `tests/test_pipeline_auto_mark_dead.py` | 9-case test matrix (all passing) |

### Tests

864 tests pass (854 existing + 9 new + 1 xfailed unchanged). New cases:
1. Happy-path: 4 jobs → 3 seen (75% exact) → 1 marked dead
2. Below-threshold skip (50% < 75%)
3. Zero-records run skip (always)
4. Aggregator skip (Adzuna stays opted-out)
5. Failed-run skip (status='error')
6. Feature-flag-off no-op
7. First-run-no-history (sweep runs since records_seen > 0)
8. Rediscovery resets flag
9. Exact-threshold boundary (75% triggers, doesn't skip)

---

## What's NOT built (deferred)

- **Aggregator 30-day age sweep** — separate follow-up. Adzuna/Reed/CoreSignal jobs that haven't been refreshed in 30 days could be auto-marked dead via a periodic cron, but the semantics differ enough that it warrants its own PR.
- **Hard delete** — explicitly out of scope. Soft-delete forever; storage is not a concern.
- **Email/Slack notification of high-volume sweeps** — no operator alerting. Watch the Dashboard panel manually for the first week.
- **Per-source sweep override** — currently one global flag + one per-adapter flag. If you want to disable sweep for ONE specific Workday source while keeping it on for the rest, that's not supported today.

---

## Behavioural notes

- **The 75% threshold uses the most recent successful run**. If the prior run had `records_seen=200` and today's run has `records_seen=180`, that's 90% — sweep proceeds. If yesterday's was a partial fetch with `records_seen=80`, today's `records_seen=200` is 250% of yesterday — sweep proceeds. Either way, the comparison is against history, not against a fixed expectation.
- **First-ever run for a source has no prior to compare against** — sweep proceeds (since `records_seen > 0`). Practically a no-op since nothing exists to mark dead, but logged in `diagnostics_blob['marked_dead']=0`.
- **`status='error'` runs never sweep**, even when `records_seen > 0`. Failed runs may have partial data; we don't trust them.
- **`records_seen=0` runs never sweep**, regardless of threshold. A zero-record run is always treated as suspicious (transient anti-bot, JS render failure, etc.).
- **Re-discovery clears the flag for ANY reason it was set**, including operator-set "Dead" UI clicks. If an operator manually marks a job dead and then the scraper finds it again, it'll come back. There's no `deleted_by` column to distinguish; if that becomes a real issue, add one as a follow-up.

---

## Operator workflow

### Daily (10 sec)

1. Open Dashboard.
2. Scroll to "Recently deleted" panel.
3. Glance at the count. If it's much higher than usual (>50/day for a single-day window), expand and spot-check.
4. If you see a row that looks alive, click "↺ Undelete" — gone in <1 second.

### Weekly (5 min)

1. SQL query for top sources by sweep volume:
   ```sql
   SELECT s.employer_name, COUNT(*) AS swept_this_week
   FROM raw_jobs r
   JOIN sources s ON r.source_id = s.id
   WHERE r.deleted_at_source_at >= NOW() - INTERVAL '7 days'
   GROUP BY s.employer_name
   ORDER BY swept_this_week DESC
   LIMIT 20;
   ```
2. Investigate any source with >10 sweeps/week — could indicate selector rot or pagination cap.

### When something looks off

- **All jobs from a source disappear at once**: check the latest `source_runs` row for that source. Look at `diagnostics_blob`. Common causes: anti-bot interstitial returned 0 jobs but adapter still reported success; pagination broke and only first page returned. Threshold guard should usually catch this.
- **A job that's clearly still posted got marked dead**: hit `POST /api/leads/{enriched_job_id}/undelete` or use the "Recently deleted" panel. Then check if the source's `last_seen_at` updates next scrape — if so, root cause was a single bad fetch.
- **You want to disable sweep for one specific source** without disabling globally: not supported in code today. Workaround: temporarily set the source's `adapter_name` to a non-opted-in adapter (e.g. `generic_site`), run scrape, switch back. Or accept transient false positives and undelete.

---

## Pre-launch backup

If you ever need to undo all sweep effects entirely:

```bash
psql prospero <<'SQL'
UPDATE raw_jobs
SET is_deleted_at_source = FALSE,
    deleted_at_source_at = NULL
WHERE deleted_at_source_at IS NOT NULL;
SQL
```

This restores every sweep-marked row to live. Pre-build snapshot for full DB rollback: `.data/backups/prospero-pre-auto-mark-dead-2026-04-29-2308.sql`.

---

## Files to know

```
configs/app.toml                                       ← feature flag
src/vacancysoft/pipelines/persistence.py               ← sweep logic
src/vacancysoft/adapters/base.py                       ← AdapterCapabilities flag
alembic/versions/0016_add_deleted_at_source_at.py      ← migration
src/vacancysoft/api/routes/leads.py                    ← /api/leads/recently-deleted, /undelete
web/src/app/page.tsx                                   ← Recently deleted panel
tests/test_pipeline_auto_mark_dead.py                  ← test matrix
~/.claude/plans/auto-mark-dead-jobs.md                 ← original design doc
```
