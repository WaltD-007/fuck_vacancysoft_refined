# Deferred work

These three chunks of in-progress work are still uncommitted on disk. They were
deferred from the recoverability commit pass because they don't block the live
system from running today, but they should land before the next clean checkout
or fresh deployment to keep the tree consistent.

Each entry lists the affected files and the acceptance criteria for "done".

---

## Ticket 1 — Commit pipeline overhaul (detail fetching, filtering, scoring, exports)

**Why this matters.** This is the biggest still-uncommitted backend block.
It contains the lead-quality logic that sits between enrichment and export.
If lost, the running pipeline degrades to its committed-baseline behaviour
(no recruiter filtering, no detail backfill, no `new_leads_only` webhook
filtering, no Workday-URL location extraction, no employer extraction from
aggregator payloads, no externalised scoring weights).

**Files to commit.**
- `src/vacancysoft/enrichers/detail_fetch.py` (modified, +358 — Workday CXS
  + SmartRecruiters APIs + JSON-LD/meta fallback for date and location)
- `src/vacancysoft/pipelines/detail_backfill.py` (new — async backfiller
  that walks `EnrichedJob` rows missing date/location and patches them)
- `src/vacancysoft/pipelines/enrichment_persistence.py` (modified, +470 —
  `_extract_employer_from_payload` for Adzuna/Reed/eFC/Google Jobs,
  `_mark_filtered` to stub-record geo/agency/title-filtered jobs without
  proceeding to enrichment, `_COMPANY_HQ` fallback dict, Workday URL
  location parser)
- `src/vacancysoft/pipelines/classification_persistence.py` (modified, +1 —
  filter that skips jobs whose `detail_fetch_status` is geo/agency/title
  filtered)
- `src/vacancysoft/pipelines/persistence.py` (modified)
- `src/vacancysoft/pipelines/scoring_persistence.py` (modified)
- `src/vacancysoft/scoring/engine.py` (modified, +46 — externalises weights
  and thresholds to `configs/scoring.toml`, adds `decision_from_score`)
- `configs/scoring.toml` (verify whether it changed; commit if yes)
- `src/vacancysoft/exporters/webhook_sender.py` (modified, +241 — adds
  `send_new_leads_to_webhook`, exponential backoff retry on 429/5xx,
  ExportRecord stamping for delivery audit)
- `src/vacancysoft/exporters/views.py` (modified, +28 — `new_leads_only_query`
  joining ExportRecord, employer-name fallback case)
- `src/vacancysoft/exporters/serialisers.py` (modified, +15 — caches
  `load_legacy_routing()`, prefers `location_city` over `location_text`)

**Acceptance criteria.**
- [ ] All ten files committed in one cohesive commit (or split into
  enrichment / scoring / exporters if that reads better in `git log`).
- [ ] `PYTHONPATH=src python3 -c "from vacancysoft.pipelines.classification_persistence import classify_enriched_jobs"` imports clean.
- [ ] `PYTHONPATH=src python3 -c "from vacancysoft.exporters.webhook_sender import send_new_leads_to_webhook"` imports clean.
- [ ] No untracked Python files remain under `src/vacancysoft/pipelines/` or
  `src/vacancysoft/enrichers/`.

**Open follow-ups (not blocking, worth a separate task).**
- The `classification_persistence.py` filter silently drops geo/agency/title-
  filtered jobs from classification, scoring, and export. No metric or log
  surfaces filtered counts. Add a counter or `pipeline status` line so the
  filter isn't invisible in production.
- `webhook_sender.py` retry backoff (2s/5s/15s) is hardcoded. Move to
  `configs/app.toml` `[exports]` block if you want to tune per receiver.

---

## Ticket 2 — Commit CLI expansion

**Why this matters.** The `prospero` binary on disk has all the new
commands the operator uses day-to-day (`db reset-pipeline`, `db fix-adapters`,
`db add-source`, `pipeline status`, `queue list/resolve/add`, `intel
dossier/campaign/prompts`). Without committing this, a fresh clone gets the
old, sparse CLI even though the rest of the stack supports the new commands.

**Files to commit.**
- `src/vacancysoft/cli/app.py` (modified, +1,733)

**Strict ordering note.** This commit will not import-check until **Ticket 1**
is also landed, because `cli/app.py` imports `send_new_leads_to_webhook`,
`backfill_detail_for_enriched_jobs`, and the filtered-status constants from
files in Ticket 1. Land Ticket 1 first, then Ticket 2.

**Acceptance criteria.**
- [ ] Single commit with the cli/app.py change.
- [ ] `PYTHONPATH=src python3 -c "from vacancysoft.cli.app import app; print('CLI imports OK')"` succeeds.
- [ ] `prospero --help` lists the new command groups (db, pipeline, queue, intel, export).

---

## Ticket 3 — Commit tests, scripts, docs, and misc

**Why this matters.** Eight untracked test files (~946 lines) have never been
in git history, so they aren't running in CI and haven't been maintained
against the chunks just landed. The scripts directory has 11 untracked debug
and audit scripts that are useful for production triage. Two changelog/note
files (`CAMPAIGN_BUILDER_CHANGELOG.md`) document recent work. Loose root
files (`run.sh`, `start.sh`, `test_quick.py`, `mockup/`, `n8n_workflows/`)
need an explicit decision: commit or delete.

**Files to commit (cluster A — tests).**
- `tests/test_adapters.py`
- `tests/test_classification.py`
- `tests/test_config.py`
- `tests/test_employer_extraction.py`
- `tests/test_export.py`
- `tests/test_location.py`
- `tests/test_recruiter_filter.py`
- `tests/test_scoring.py`

**Files to commit (cluster B — scripts).**
- `scripts/audit_adapter_locations.py` (new)
- `scripts/audit_adapter_locations_live.py` (new)
- `scripts/debug_generic_location.py` (new)
- `scripts/debug_successfactors_location.py` (new)
- `scripts/export_taxonomy_xlsx.py` (new)
- `scripts/run_generic_backlog.py` (new)
- `scripts/test_403_alternatives.py` (new)
- `scripts/test_full_pipeline.py` (new)
- `scripts/test_generic_batched.py` (new)
- `scripts/test_generic_boards.py` (new)
- `scripts/test_successfactors.py` (new)
- `scripts/audit_generic_quality.py` (modified)
- `scripts/check_generic_access.py` (modified)

**Files to commit (cluster C — docs and ops).**
- `CAMPAIGN_BUILDER_CHANGELOG.md` (new)
- `mockup/` (design assets — commit unless purely scratch)
- `start.sh` (boots Redis + Postgres + API + worker + frontend together)
- `run.sh` (dev/onboarding script)
- `test_quick.py` (loose smoke script — review before committing)

**Files needing a decision.**
- `n8n_workflows/` — earlier you said you're ditching n8n. Default
  recommendation is to delete, but it's still on disk untouched. Pick one:
    - `git rm -rf n8n_workflows/` if truly gone, or
    - `git add n8n_workflows/` if you want to keep the JSON workflows in
      history before purging.

**Acceptance criteria.**
- [ ] Tests committed and `PYTHONPATH=src python3 -m pytest tests/ -q` runs
  (pass or fail noted — fixing failures is a separate task).
- [ ] Scripts committed.
- [ ] Docs and ops shell scripts committed.
- [ ] `n8n_workflows/` decision made and acted on.
- [ ] `git status -s` returns empty (or only ignored items).

---

## Out of scope for these tickets

- The Permanent/Contract employment-type classification on
  `claude/priceless-lovelace` — cherry-pick onto `chatgpt/adapter-updates`
  is a follow-up step.
- Resolving the Risk vs Quant taxonomy overlap flagged in the
  in-progress audit (keywords `model risk`, `model validation`, `quant`
  appear in both categories).
- Migrating any local SQLite DB still using the old `.data/vacancysoft.db`
  filename to `.data/prospero.db`.
