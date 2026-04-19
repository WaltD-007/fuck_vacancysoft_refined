# CI

Continuous integration for Prospero. One GitHub Actions workflow —
[`.github/workflows/ci.yml`](workflows/ci.yml) — runs on every push
and every pull request. Goal: catch regressions automatically so
nobody has to remember to run `pytest` / `tsc` / `ruff` before
pushing.

## What runs

Two parallel jobs:

### `backend` (Python)

1. `pip install -e ".[dev]"` — the same editable install the human
   uses locally via `run.sh`.
2. `pytest -q --tb=short` — must stay at **359 passed, 1 xfailed**
   (see below). Uses `strict=xfail` on the one expected failure so
   a surprise pass also flags.
3. `ruff check src tests` — currently `continue-on-error: true`
   because no explicit rule set has been agreed. Defaults are mild
   (E, F). Tighten when ready (see Tuning below).

Typical wall time: ~60–90 s on a warm cache.

### `frontend` (Next.js)

1. `npm ci` inside `web/`.
2. `npx tsc --noEmit` — same command the human runs after every
   component extraction. `noEmit` is set in `tsconfig.json`, so
   nothing hits disk.

Typical wall time: ~30–45 s on a warm cache.

## Triggers

- **Push** on any branch except `claude/**` (agent worktrees push
  to their own branches; skipping those avoids CI storms during a
  long refactor session).
- **Pull request** (regardless of branch).
- **Manual dispatch** — you can re-run CI from the Actions tab via
  the `Run workflow` button.

A `concurrency` group cancels older runs when a newer commit lands
on the same ref, so only the head commit's green-or-red status
matters.

## Running the same checks locally

The whole workflow is trivially re-runnable on your laptop — this
is deliberate, so "green locally" means "green in CI" 99% of the
time. Copy-paste from the repo root:

```bash
# Backend
pip install -e ".[dev]"
pytest -q --tb=short
ruff check src tests     # expect some output; non-blocking in CI

# Frontend
cd web && npm ci && npx tsc --noEmit
```

No Playwright install required. No Redis required (the CI never
spins up a real server).

## The xfail

[`tests/test_classification.py::TestIsRelevantTitle::test_relevant[Pricing Actuary]`](../tests/test_classification.py)
is marked `strict=xfail`. The test asserts Pricing Actuary is
relevant; the taxonomy blocklist deliberately excludes actuarial
titles. One of them is wrong, and resolving the disagreement is
TODO ticket 6 in [`docs/TODO.md`](../docs/TODO.md).

`strict=xfail` means:

- **Still failing** → counts as "expected failure", CI stays green.
- **Starts passing** → CI fails, prompting somebody to remove the
  marker (because the blocklist or the test has been fixed and we
  should know).

## Tuning ruff

The ruff step is `continue-on-error: true` so it never blocks the
run at landing time. Two paths to tighten it:

1. **Flip to blocking**: delete `continue-on-error: true` from the
   ruff step in `ci.yml`. Do this once the team is comfortable with
   the diff produced by `ruff check src tests` locally.
2. **Pick a rule set**: add `select = [...]` under `[tool.ruff]` in
   `pyproject.toml`. Common starters: `select = ["E", "F", "I"]`
   (pycodestyle errors + pyflakes + isort). Run `ruff check --fix`
   once to auto-fix trivial issues before landing.

Either change is safe to land in a small PR once agreed.

## Troubleshooting

### "CI passed locally but failed in GitHub"

Usually one of:

- **OS difference** — GitHub uses Ubuntu, most contributors are on
  macOS. File paths, case sensitivity, and shell built-ins all
  differ. If a test relies on a macOS-only command, add a runtime
  guard or move it into an OS-specific test file.
- **Uncached dependency** — a package you installed locally weeks
  ago isn't in `pyproject.toml` or `web/package.json`. Check `pip
  freeze` / `npm ls` against the files.
- **Timezone / clock** — tests that compute `now - 24h` can behave
  differently if the CI runner's clock is off from your laptop by
  a large amount. Pin times in tests with `datetime.utcnow()` or
  `freezegun` rather than the wall clock.

### Re-running CI without a new commit

From the Actions tab in GitHub, pick the failed run and hit
"Re-run jobs". For a single job, use "Re-run failed jobs" — saves
a full Python/Node reinstall cycle.

### Adding a new check

Add a new step inside the existing `backend` or `frontend` job —
don't add a third job unless you genuinely need a different OS /
language. Keep the total wall time under ~3 minutes; CI that takes
10 minutes is CI people ignore.

## Things this CI does NOT do (yet)

- **Migrations** — no `alembic upgrade head` step. The test suite
  uses an in-memory SQLite with `Base.metadata.create_all`, so it's
  not needed. Add only if a future migration contains logic worth
  validating on every PR.
- **Playwright smoke** — skipped to keep wall time down. `scripts/
  test_live_scrapers.py` et al exist for manual exercise.
- **Deploy** — out of scope; this is a quality gate, not a release
  pipeline.
- **Coverage** — no `pytest-cov` run. Add if / when the team
  agrees on a coverage target.

## Relation to the manual refactor process

Weeks 1–5 of the refactor session used `pytest + tsc --noEmit +
curl smoke tests` as the manual per-step verification. Week 6 (this
file) lifts the first two into CI so future refactors can trust the
green tick instead of re-running everything by hand.

See [`docs/refactoring/week6-ci.md`](../docs/refactoring/week6-ci.md)
for the landing commit's rationale and rollback notes.
