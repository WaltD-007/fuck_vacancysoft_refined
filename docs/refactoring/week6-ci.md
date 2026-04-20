# Week 6 — Add minimal CI

Adding a GitHub Actions workflow that runs the same checks the human
was running manually after every refactoring step in Weeks 3–5.

## Starting baseline

- Branch: `chatgpt/adapter-updates`
- Starting HEAD: `163d8e9` (after the Pricing Actuary xfail landing)
- `pytest` on main branch: 359 passed, 1 xfailed (Pricing Actuary)
- `tsc --noEmit` under `web/`: clean
- `ruff` not yet installed locally; dev dep already declared in
  `pyproject.toml:27`
- Zero CI configuration in the repo before this week

## What landed

### `.github/workflows/ci.yml`

One workflow with two parallel jobs:

| Job | Steps | Timeout |
|---|---|---|
| `backend` | pip install + pytest + ruff (non-blocking) | 10 min |
| `frontend` | `npm ci` + `tsc --noEmit` | 10 min |

Triggers: push (except `claude/**` agent branches), pull_request,
manual dispatch. Concurrency group cancels superseded runs on the
same ref.

### `.github/README.md`

A short operator guide that covers:
- what each job does and why
- how to run the same commands locally
- what the `strict=xfail` on Pricing Actuary means
- how to tighten ruff (currently `continue-on-error: true`)
- common macOS-vs-Ubuntu gotchas
- what this CI intentionally does NOT do (migrations, Playwright,
  deploy, coverage)

### Rationale for each design choice

- **Two jobs, not one** — backend and frontend are independent and can
  run in parallel, halving wall time. They share no state and a
  failure in one shouldn't block feedback on the other.
- **No OS matrix** — only Ubuntu. Adding macOS / Windows triples CI
  minutes for a team where everyone develops on macOS already.
- **No Python matrix** — only 3.12 (the project's pinned minimum via
  `pyproject.toml`). Adding 3.13 would be cheap; deferred until a
  real 3.13 bug motivates it.
- **No Playwright install** — ~200 MB download, ~30 s cold. No test
  needs it. `scripts/test_live_scrapers.py` et al exist for manual
  exercise; those stay out of CI deliberately because they hit real
  job boards.
- **ruff non-blocking initially** — `pyproject.toml` has only
  `line-length` and `target-version` set, no `select = […]`. Default
  rules (E, F) are mild, but landing a blocking ruff without
  consensus on the rule set risks failing the first real PR for
  stylistic reasons. Flip after the team agrees — see Tuning in the
  README.
- **`strict=xfail` on Pricing Actuary** — a surprise pass should fail
  CI so someone removes the marker. Without `strict`, a fixed test
  silently keeps passing and the xfail annotation decays into cruft.
- **`branches-ignore: claude/**`** — agent worktrees push to their
  own branches during refactor sessions. CI on every agent-branch
  commit would queue up dozens of pointless runs.

## Verification

- `cat .github/workflows/ci.yml` — YAML parses (verified by the
  GitHub Actions schema in the editor; no runtime check possible
  before a push to GitHub).
- `pytest -q --tb=short` locally: 359 passed, 1 xfailed, exit 0.
- `cd web && npx tsc --noEmit` locally: clean, exit 0.
- First real CI run will happen the next time someone pushes to a
  non-`claude/**` branch or opens a PR.

## Rollback

- Delete the workflow: `git rm .github/workflows/ci.yml`. GitHub
  Actions infers workflows from the filesystem; removing the file
  stops the runs immediately.
- The `.github/README.md` is safe to leave in place even without a
  workflow — it documents the intent.

For a full week revert: `git revert <this-commit-sha>`.

## Follow-ups (out of scope this week)

- Flip ruff from `continue-on-error: true` to blocking once the team
  reviews the baseline output.
- Add `pytest-cov` + a coverage threshold if / when agreed.
- Cache the `.venv` directory for faster cold starts on tight
  budgets.
- Consider a `release` workflow (tag → build → publish) if / when
  Prospero moves beyond single-user deployment.
