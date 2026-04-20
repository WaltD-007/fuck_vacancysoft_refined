# Runbook — deploy a fix to a scraper adapter

Use this when: **a specific job board has stopped scraping correctly and you've
identified the fix in the adapter code**. Typical examples:

- generic browser adapter returning 0 jobs on a site it used to work on
- a specific ATS adapter (Workday, Greenhouse, etc.) breaking after a vendor update
- SuccessFactors pagination missing page 2+

This runbook is **not** for:

- schema changes (those add an Alembic migration — different workflow)
- frontend changes only (no worker rebuild needed)
- infrastructure changes (Bicep/Container Apps changes — see `deployment_plan.md`)

If you're unsure which bucket your change falls into, ask Claude.

---

## Prerequisites (do these once, not every time)

Verify you can do each of these before starting a real fix. If any fail, stop
and resolve them first — fixing them mid-deploy is how outages happen.

```bash
# 1. Logged into Azure CLI, pointing at the right subscription
az account show
# expected: subscription id matches your Prospero subscription

# 2. Logged into the Container Registry for image push
az acr login --name <your-acr-name>
# expected: "Login Succeeded"

# 3. You can pull the current running image tags
az containerapp revision list --name ca-worker \
    --resource-group rg-prospero-prod -o table
# expected: a table with the current active revision at the top

# 4. You can tail worker logs
az containerapp logs show --name ca-worker \
    --resource-group rg-prospero-prod --tail 50
# expected: recent log lines, not an auth error

# 5. GitHub CLI is authed (needed for opening the PR)
gh auth status
# expected: "Logged in to github.com as <you>"

# 6. Docker + docker compose work locally
docker compose version
# expected: a version string, not "command not found"
```

If any step fails, fix it **now**, not during the deploy.

---

## Step 1 — Reproduce the problem locally

Never ship a fix without reproducing the bug first. If you can't reproduce it,
you don't actually know what's wrong.

```bash
cd /Users/antonyberou/Documents/Work\ Stuff/AI\ Stuff/Python\ projects/Useful\ Code/fuck_vacancysoft_refined

# Make sure your local DB has the broken source seeded
prospero db stats
# expected: source_count > 0

# Run the adapter against the live site and see what it actually does
prospero pipeline discover --source-key "<source-key-of-broken-board>" --limit 5 --dry-run

# Expected failure modes:
#   - returns 0 jobs when the site clearly has jobs → discovery is broken
#   - returns jobs but titles are "None" or garbled → parsing is broken
#   - hangs for 60s+ → the adapter's wait-for-selector is wrong
#   - HTTP 403 / Cloudflare challenge page → scraping strategy needs upgrade
```

**Write down one sentence describing exactly what's wrong**, e.g.:
> "Generic browser adapter on acme.com returns 0 jobs because the CSS selector
> `.job-posting` changed to `[data-testid='posting-card']` after their Oct
> redesign."

You'll need this for the commit message. If you can't write that sentence,
keep investigating — don't start editing yet.

---

## Step 2 — Fix the adapter

Most adapter fixes are in `src/vacancysoft/adapters/`. The generic browser
adapter specifically is `src/vacancysoft/adapters/generic_browser.py`.

- Edit the adapter file.
- **Add or update a test in `tests/test_adapters.py`** that would have caught
  this bug. A fix without a test is a fix that will regress.

If you're not sure which file holds the adapter you need, open it on Claude
and ask "which file handles the X board".

---

## Step 3 — Prove the fix works locally

Two checks, in order. Both must pass.

### 3a. Unit / adapter tests

```bash
pytest tests/test_adapters.py -q
```

Expected: all green. If the previously-xfailed Pricing Actuary test shows up,
ignore it — that's unrelated (see `docs/TODO.md` ticket 6).

### 3b. Real run against the actual site

```bash
# Re-run discovery against the real board — this is the one that tells you
# if the fix actually works against live HTML, not just against your
# assumptions about live HTML
prospero pipeline discover --source-key "<source-key>" --limit 5
```

Expected: jobs > 0, titles look sensible, no errors in the log.

If this still fails: your fix is incomplete. Go back to Step 2. **Do not ship
a fix that doesn't work locally.**

---

## Step 4 — Prove the fix works in the prod-like container

The worker image runs on Ubuntu with a different Chromium build than your
Mac's local Playwright. About 1 in 10 adapter fixes behaves differently on
the Linux Chromium — headless mode quirks, font rendering, locale. Catch
that now, before deploy.

```bash
# Build and run the full prod topology locally
docker compose -f docker-compose.prod.yml up --build

# In another terminal, watch the worker logs
docker compose -f docker-compose.prod.yml logs -f worker

# Trigger the broken source from the UI:
# → open http://localhost
# → log in (local compose uses no auth)
# → /sources page → find the board → click "Scrape"
# → watch the worker logs until you see the run complete
```

Expected:
- Worker logs show adapter running, jobs discovered, no tracebacks
- The source's card in the UI updates with a new run timestamp
- Clicking into the source shows the newly-discovered jobs

If the container run behaves differently from your `prospero pipeline discover`
run (common: works on Mac, times out in container), the problem is usually
one of:
- a selector that depends on JS that loads at different speed in containers
- a `wait_for_selector` timeout that's fine locally but too tight for the
  slower container CPU
- the site detecting headless Chrome and serving different content

When this happens, ask Claude to compare the two runs' screenshots/HTML.

Shut down the compose stack when you're done:

```bash
docker compose -f docker-compose.prod.yml down
```

---

## Step 5 — Commit and open a PR

```bash
git checkout -b fix/adapter-<board-name>
git add src/vacancysoft/adapters/<file>.py tests/test_adapters.py
git commit -m "Fix <board-name> adapter — <one-sentence summary>"
git push -u origin fix/adapter-<board-name>
gh pr create --fill
```

Then watch CI in the GitHub UI (or `gh pr checks --watch`). CI runs pytest,
ruff, and `tsc --noEmit` on the frontend. All must be green.

**Do not merge with failing CI.** If CI fails and it's your adapter test:
your fix broke something. If CI fails and it's unrelated: fix that separately
or coordinate with whoever is already working on it.

Once green, merge the PR.

---

## Step 6 — Watch the deploy

On merge to `main`, `.github/workflows/deploy.yml` fires automatically. Watch
it:

```bash
gh run watch
```

You'll see, in order:

1. **`build-images`** — `az acr build` for each of the four Dockerfiles
   (api, migrate, worker, web). Each takes ~30–120s. All four are built
   even though you only changed Python code — that's expected and safe.
2. **`run-migrations`** — `az containerapp job start --name job-migrate`
   and wait for exit 0. For an adapter fix with no migration changes, this
   runs `alembic upgrade head` and exits in 1–2s with "no new migrations".
   **If this step fails, the deploy stops.** Fixing this is a separate
   procedure — see "Migration failure" under "Recovery" below.
3. **`deploy-apps`** — `az containerapp update --image <...>` for
   `ca-api`, `ca-worker`, `ca-web` in parallel. Each takes ~60–180s as
   new revisions come up, pass health checks, and take traffic.

Total wall-clock from merge to "fix is live": ~6–10 minutes typically.

If any step fails, the workflow shows the failing step in red. Click into
it, read the actual error message. 90% of the time it's one of:
- ACR auth expired → `az acr login` and re-run
- a flaky Azure API call → hit "re-run failed jobs"
- an actual problem with your change → fix and push a new commit

---

## Step 7 — Verify the fix in production

Do not trust "the deploy succeeded" to mean "the fix works". Verify
directly.

```bash
# Option A — via the UI
# → log in to https://prospero.<corp>.com (Entra ID prompt)
# → /sources page → find the board you fixed
# → click "Scrape"
# → refresh a minute later; confirm the jobs count went up and the
#   last-run status is "success"

# Option B — via the logs
az containerapp logs show --name ca-worker \
    --resource-group rg-prospero-prod --follow --tail 100
# then trigger the scrape from the UI and watch the adapter log
# its work in real time
```

What you want to see:
- adapter starts, finds jobs, commits them, run ends cleanly
- no tracebacks, no "0 jobs discovered"
- the source's card in `/sources` shows green "success" status

If the fix works on prod: **done.** Close the loop by dropping a note in
wherever you track work (`docs/TODO.md`, Linear, etc.) that the bug is
resolved.

If the fix **doesn't** work on prod but did locally: the bug is
environment-specific. Most likely:
- Azure's IP is blocked by the site (see risk #1 in `deployment_plan.md`)
- Playwright version drift between local and container image
- Site serves different content to datacenter IPs

Don't try to fix this in prod. Reproduce locally first — use a Linux VM or
a cloud runner if you can't repro on Mac.

---

## Step 8 — Rollback (only if needed)

If the fix is actively making things worse in prod (e.g., the adapter now
crashes the worker on every invocation), roll back before debugging.

```bash
# 1. Find the previous good worker revision
az containerapp revision list --name ca-worker \
    --resource-group rg-prospero-prod -o table
# look for the revision that was active before the bad deploy.
# revision names look like: ca-worker--<suffix>

# 2. Pin 100% traffic to the previous revision
#    (this is for the API; the worker doesn't serve traffic but
#     the command stops the new revision and activates the old one)
az containerapp revision activate \
    --resource-group rg-prospero-prod \
    --name ca-worker \
    --revision ca-worker--<previous-good-suffix>

az containerapp revision deactivate \
    --resource-group rg-prospero-prod \
    --name ca-worker \
    --revision ca-worker--<bad-new-suffix>

# 3. Do the same for ca-api if the bad deploy also affected it
#    (an adapter-only fix usually doesn't, since the API doesn't
#     execute adapter code — but rebuilds still ship a new api
#     image, so if the api is misbehaving, roll it back too)
```

Rollback takes ~30s. **You do not need to revert the git commit** — just
leave main as-is and open a new branch/PR to fix the real problem. The
next successful deploy replaces the rollback.

---

## Recovery — common failures and what to do

### "Migration failure" (Step 6, step 2 fails)

The deploy has stopped before touching the running apps. Production is
**still on the old code + old schema**. Safe.

Options:
1. The migration has a bug — fix it, push a new commit, re-run the deploy.
2. The migration conflicts with data state — coordinate a fix with whoever
   owns the data. Do not re-run until resolved.

Do **not** run `alembic upgrade head` manually against the prod DB to "just
get past it". If the migration is wrong, running it manually makes the
rollback impossible.

### "ACR build failed"

Usually a transient Azure issue. Re-run the workflow. If it fails twice
with the same error, read the error: if it's "repository not found" your
ACR name is wrong in the workflow; if it's "auth required" your federated
identity for GitHub Actions has expired.

### "New revision provisioning failed"

The app container is crashing on startup. Check logs:

```bash
az containerapp logs show --name ca-<app> \
    --resource-group rg-prospero-prod \
    --tail 100
```

Common causes:
- Missing env var (a secretRef to a Key Vault secret that doesn't exist)
- DB connection string broken (check the Key Vault value)
- Startup hook crashed (api only — look for the self-heal sweep log line)

Rollback to the previous revision (Step 8) while you debug.

### "Fix works locally but scrape returns 0 jobs in prod"

Almost always IP-based blocking. Confirm with:

```bash
# Trigger the scrape, then immediately look for the adapter's
# actual HTTP response in the logs
az containerapp logs show --name ca-worker \
    --resource-group rg-prospero-prod --follow --tail 200 \
    | grep -i "status\|403\|403\|cloudflare\|blocked"
```

If you see 403s or Cloudflare challenges from Azure but not locally, the
site is blocking datacenter IPs. Options:
- route that specific adapter through a residential proxy (config-level
  change, no code)
- mark the source as "aggregator-only" and rely on Adzuna/Reed picking it
  up
- accept the coverage loss

This is a separate workstream, not a hotfix.

### "I merged but no deploy ran"

Check:

```bash
gh run list --workflow=deploy.yml --limit 5
```

If nothing appears, the workflow may be disabled, or your PR merged to a
branch other than `main`. Fix the trigger condition in the workflow file.

---

## Quick reference card

| What you did | What runs | Typical time |
|---|---|---|
| Push to a PR branch | CI only (pytest, ruff, tsc) | 2–3 min |
| Merge PR to main | Full deploy workflow | 6–10 min |
| `docker compose -f ...prod.yml up --build` | Local rebuild everything | 3–5 min (first), 30–60s (subsequent) |
| `az containerapp revision activate` | Rollback | ~30s |

Keep this file open in a tab the first few times you do an adapter
deploy. After a few iterations you'll have the rhythm and can skim it.
