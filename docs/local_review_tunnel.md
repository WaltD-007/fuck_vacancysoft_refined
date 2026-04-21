# Local review tunnel — share Prospero with a colleague

> **Purpose:** hand someone else (another recruiter, a reviewer, a
> prospective user) a URL they can open in their browser to click
> around your locally-running Prospero. For a one-session demo
> before you have a proper hosted environment.
>
> **Cost:** £0 with ngrok's free tier. **Setup:** <5 min from a clean
> Mac state. **Recovery:** one Ctrl-C.

---

## TL;DR — paste-ready commands

Prerequisites met once per machine:
```bash
brew install ngrok                           # one-time install
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined" && git pull --ff-only
```

**Four terminals, run in this order:**

```bash
# Terminal 1 — FastAPI (loopback only, never exposed)
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined"
uvicorn vacancysoft.api.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# Terminal 2 — ARQ worker (only needed if colleague will trigger
# anything that enqueues jobs: dossier regen, campaign gen, etc.)
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined"
arq vacancysoft.worker.settings.WorkerSettings
```

```bash
# Terminal 3 — Next.js frontend (the only service exposed)
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/web"
npm run dev
```

```bash
# Terminal 4 — ngrok tunnel (pick any passphrase, no spaces)
ngrok http 3000 --basic-auth "reviewer:prospero-demo-2026"
```

Grab the `https://*.ngrok-free.app` URL from terminal 4. Send to colleague with:
```
URL: https://<random>.ngrok-free.app
User: reviewer
Password: prospero-demo-2026
```

**Kill:** `Ctrl-C` in each terminal. Tunnel URL is dead the instant you kill terminal 4.

**Verify nothing left listening:**
```bash
lsof -i :3000 -i :8000 | grep LISTEN   # should return nothing after Ctrl-C × 3
```

---

**Never tunnel** these ports:
- `:5432` — Postgres (database)
- `:6379` — Redis (queue)
- `:8000` — FastAPI (it's reached server-side via Next.js rewrite, not by the colleague's browser)

Only `:3000` goes through ngrok. Everything else stays on loopback.

---

## Before you start — what the colleague will see

- **Everything.** Prospero has **no authentication** — anyone with
  the tunnel URL has operator-equivalent access: they can read every
  lead, add/edit sources, paste URLs, and (if your Microsoft Graph
  config is live — it isn't yet) send emails on your behalf.
- **Your real local data.** Whatever leads, dossiers, and campaigns
  are currently in your Postgres will be visible. If there's
  anything you wouldn't want a colleague to see, either cherry-pick
  what you want to show or spin up a second empty Postgres DB for
  the session (see §6).

Defence in depth: always pair the tunnel with **basic auth** (ngrok
has it built-in, one flag). That at least stops a random URL scan
from landing on an unauthenticated Prospero.

---

## 1. One-time prerequisites

```bash
# Homebrew installs — each is a one-liner, none needs a signup.
brew install ngrok
# If you prefer Cloudflare Tunnel instead (no signup required for
# ephemeral tunnels — but URL is random and changes on restart):
# brew install cloudflared
```

For Tailscale (the private-VPN alternative — needed if you want a
recurring "come look whenever" setup): skip for now; covered in §7.

---

## 2. The four-terminal launch

Each terminal stays open until you kill it. Run them in this order:

### Terminal 1 — FastAPI

```bash
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined"
uvicorn vacancysoft.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Bind to `127.0.0.1` (not `0.0.0.0`) — the API is reached by the
Next.js rewrite, not by the colleague's browser directly. Binding to
loopback means even if the tunnel config was wrong somehow, the API
isn't reachable from the internet.

### Terminal 2 — ARQ worker

```bash
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined"
arq vacancysoft.worker.settings.WorkerSettings
```

Required if your colleague will interact with features that enqueue
jobs (dossier regeneration, campaign generation, the outreach stack's
scheduled sends in dry-run). Skip this terminal if you just want to
show the read-only surfaces.

### Terminal 3 — Next.js frontend

```bash
cd "/Users/antonyberou/Documents/Work Stuff/AI Stuff/Python projects/Useful Code/fuck_vacancysoft_refined/web"
npm run dev
```

Starts Next.js on `:3000`. The `rewrites()` in `next.config.ts` will
proxy `/api/*` to `http://localhost:8000/api/*` server-side, so the
colleague's browser only ever talks to `:3000`.

First run after a fresh clone: `npm install` before `npm run dev`.

### Terminal 4 — ngrok tunnel

```bash
ngrok http 3000 --basic-auth "reviewer:<something-you-pick>"
```

Replace `<something-you-pick>` with a short passphrase (no spaces —
quotes are already there but spaces trip up shell escaping). You'll
get output like:

```
Session Status  online
Forwarding      https://abcd-1234-56-78.ngrok-free.app -> http://localhost:3000
```

**Share**: the URL + the colleague's credentials (`reviewer` /
`your-passphrase`). They open the URL, enter credentials at the
browser prompt, land on Prospero.

---

## 3. What to send the colleague

Template:

> Hey [name],
>
> Prospero demo is live at https://abcd-1234-56-78.ngrok-free.app
> Username: `reviewer`
> Password: `<passphrase>`
>
> A couple of things to note before you click around:
> - This is my laptop, not a hosted environment, so bear with
>   any slowness — when I close the tunnel the URL dies.
> - No risk of sending real mail at the moment — the Microsoft
>   Graph integration is in dry-run mode.
> - If you spot a lead you want me to look at more carefully or
>   a bug that needs fixing, drop me a message with the URL and
>   I'll follow up after.
>
> Let me know when you're done and I'll close the tunnel.

---

## 4. Recovery — shut it down

1. `Ctrl-C` in each of the four terminals, in any order.
2. That's it. The ngrok URL is dead the second you kill terminal 4.

If you want to be certain nothing is still listening:

```bash
lsof -i :3000 -i :8000 | grep LISTEN
# Should return nothing.
```

---

## 5. What to absolutely not do

- **Don't tunnel port 5432** (Postgres) or **6379** (Redis). The
  whole point of the Next.js rewrite is the colleague's browser
  only ever reaches port 3000. Never tunnel the database or queue.
- **Don't publish an ngrok URL anywhere public** (Slack #general,
  Twitter, a customer email). Even with basic auth it's a live
  footprint.
- **Don't skip basic auth.** An unauthed ngrok URL will be probed
  by scanners within hours — they run continuous scans against
  ngrok's domain.

---

## 6. Demo sandbox: spin up an empty Postgres just for the session

If you'd rather not show real data, run a second Postgres on a
non-standard port, seed it with a handful of cherry-picked sources,
and point Prospero at it via `DATABASE_URL`.

Quick path:

```bash
# Start a throwaway Postgres in Docker (macOS Docker Desktop needed)
docker run --rm -d --name prospero-demo \
  -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=prospero_demo \
  -p 5433:5432 postgres:17

# Create schema + copy the public-ish tables from your real DB
export DATABASE_URL="postgresql://postgres:demo@localhost:5433/prospero_demo"
alembic upgrade head

# Copy a handful of rows over (sources + raw_jobs + enriched_jobs +
# intelligence_dossiers + campaign_outputs) — pick by lead_score or a
# specific company:
pg_dump -t sources -t raw_jobs -t enriched_jobs -t intelligence_dossiers \
        -t campaign_outputs prospero \
  | psql "$DATABASE_URL"

# Start the FastAPI pointing at the demo DB (rest of the four-terminal
# flow stays the same — DATABASE_URL propagates via the env var):
uvicorn vacancysoft.api.main:app --host 127.0.0.1 --port 8000 --reload
```

When done: `docker stop prospero-demo` (auto-removes the container
because of `--rm`). The real DB is untouched.

---

## 7. Long-term alternative: Tailscale (recurring review access)

For a "come look at it whenever" relationship rather than a one-off
demo, Tailscale is a better fit than a public tunnel:

- Both parties install Tailscale from https://tailscale.com/download
- Your Prospero host: `tailscale serve https / http://localhost:3000`
- Your colleague's machine on the same tailnet reaches it at
  `https://<your-hostname>.tailnet-name.ts.net` — works only for
  signed-in Tailscale users, zero public exposure.
- Survives reboots. No URL to re-share each time.

Out of scope for a one-off — but if the demos become regular, flip
to this.

---

## 8. When you stand up the Azure Container Apps deployment

At that point, this tunnel workflow is retired. Your colleague gets
a stable URL (e.g. `prospero.barclaysimpson.com`) behind Entra ID
auth via the Container App's built-in authentication. See
[deployment_plan.md](./deployment_plan.md) for the topology and
[launch_plan.md](./launch_plan.md) items 1D + 2B for the remaining
infra work.

Until then: the four-terminal tunnel above is good enough for any
number of one-off reviews.
