# LLM provider switching (OpenAI ↔ DeepSeek)

Prospero's intelligence pipeline (dossier + campaign generation) can
run on either OpenAI or DeepSeek. This is a **config flip, not a code
change** — flip two booleans in [`configs/app.toml`](../configs/app.toml)
and restart the worker.

## The three LLM calls and where they can run

| # | Call | File:line | Handles PII? | Runs on |
|---|---|---|---|---|
| 1 | Main dossier analysis | [`dossier.py:174-189`](../src/vacancysoft/intelligence/dossier.py) | No (analytical, no named people) | **Configurable** via `use_deepseek_for_dossier` |
| 2 | Hiring-manager search | [`dossier.py:198-213`](../src/vacancysoft/intelligence/dossier.py) | **Yes** — returns named individuals from LinkedIn | **Hard-wired to OpenAI** — no toggle |
| 3 | Campaign email generation | [`campaign.py:95-114`](../src/vacancysoft/intelligence/campaign.py) | No (generic archetypes, no names) | **Configurable** via `use_deepseek_for_campaign` |

Call 2 has no config knob on purpose. Moving hiring-manager lookups —
which request named real people via web search — to a different
provider is a policy decision that should happen in a reviewed code
change, not by editing a TOML.

## How to switch

### To use DeepSeek for dossier + campaign

1. Confirm `DEEPSEEK_API_KEY` is set in `.env`. (If not, get a key at
   <https://platform.deepseek.com/api_keys> and add it.)
2. Edit [`configs/app.toml`](../configs/app.toml):
   ```toml
   [intelligence]
   use_deepseek_for_dossier  = true
   use_deepseek_for_campaign = true
   ```
3. Restart the worker (`run.sh` restart, or kill + relaunch the ARQ
   process).
4. Next dossier / campaign call uses DeepSeek. Existing rows in
   `intelligence_dossiers` / `campaign_outputs` are unaffected — the
   `model_used` column records which provider + model produced each
   row.

### To switch back to OpenAI

Set both toggles back to `false`, restart. Nothing else changes.

### To A/B test

Flip one at a time. For example, `use_deepseek_for_campaign = true`
with `use_deepseek_for_dossier = false` sends campaigns through
DeepSeek while keeping dossiers on OpenAI. The cost split will show
in the per-row `cost_usd` columns.

## Models used

### OpenAI side (default)

From the `[intelligence]` block:

```toml
dossier_model              = "gpt-5-mini"
hm_search_model            = "gpt-5.2"      # hard-wired OpenAI
campaign_model             = "gpt-5-mini"
campaign_fallback_model    = "gpt-4o"
```

### DeepSeek side (when toggles are on)

```toml
dossier_model_deepseek            = "deepseek-reasoner"  # R1
campaign_model_deepseek           = "deepseek-reasoner"  # R1
campaign_fallback_model_deepseek  = "deepseek-chat"      # V3
```

Both active calls use `deepseek-reasoner` (R1) — DeepSeek's
highest-performance reasoning model. The campaign fallback uses
`deepseek-chat` (V3) because reasoning models can burn the entire
`max_tokens` budget on internal chain-of-thought and return empty
visible content; falling back to a non-reasoning model avoids the
same trap inside the same provider family.

## Caveats when DeepSeek is on

### 1. No web search

DeepSeek has no web-search tool equivalent to OpenAI's
`web_search_preview`. When a call passes `web_search=True` and
`provider=DEEPSEEK`, the provider layer silently drops the flag and
logs a warning. The call goes to plain chat completions and the
model answers from its training data only.

**Who this affects**: the main dossier call (Call 1). It currently
uses web search to pull current company news into the
`company_context` section. On DeepSeek, that section will reflect
training-data knowledge instead.

**Mitigation**: the surrounding dossier sections (core problem,
spec risk, candidate profiles, lead score) rely more on the job
description itself than on the web, so they degrade less. If the
company context specifically needs to be current, stick to OpenAI
for dossier.

### 2. No `reasoning_effort` knob

OpenAI's GPT-5 / o1 / o3 reasoning models accept a
`reasoning_effort` parameter (`"low"` / `"medium"` / `"high"`).
DeepSeek's reasoner has a fixed internal reasoning budget — no
equivalent knob. When `reasoning_effort` is passed with
`provider=DEEPSEEK`, the provider layer silently ignores it
(debug-logged).

**Implication**: if you were tuning
`dossier_reasoning_effort` / `campaign_reasoning_effort` to control
cost on OpenAI, those knobs stop doing anything on DeepSeek. DeepSeek
cost is controlled by model choice alone (`deepseek-reasoner` vs
`deepseek-chat`).

### 3. Structured output still works

`response_format={"type": "json_object"}` — the mode the campaign
call uses — is fully supported by DeepSeek's OpenAI-compatible
endpoint. No workaround needed.

### 4. Retry and timeout behaviour are identical

The provider layer mirrors `client.py`'s retry policy: 3 attempts,
backoff 2 s / 5 s / 15 s, retryable on `APITimeoutError` and
`RateLimitError`. A DeepSeek outage will look identical to an
OpenAI outage in the logs.

## Cost accounting

The `intelligence_dossiers` and `campaign_outputs` tables have
`cost_usd` and `model_used` columns that accept any model string.
DeepSeek rows populate the same fields — no migration needed.

[`pricing.py`](../src/vacancysoft/intelligence/pricing.py) has
entries for both providers' models in a shared `PRICING` table;
`compute_cost()` resolves by longest-prefix match, so
`deepseek-reasoner` and `gpt-5-mini` are disambiguated correctly.

**Rough cost comparison** (per 1M tokens, input / output):

| Model | Input | Output | Cost per 1M in+out mix (50/50) |
|---|---:|---:|---:|
| gpt-5-mini | $0.25 | $2.00 | ~$1.13 |
| gpt-5.2 | $1.25 | $10.00 | ~$5.63 |
| gpt-4o | $2.50 | $10.00 | ~$6.25 |
| deepseek-reasoner (R1) | $0.55 | $2.19 | ~$1.37 |
| deepseek-chat (V3) | $0.27 | $1.10 | ~$0.69 |

Reasoner models produce more output tokens than non-reasoning
models (they emit their chain-of-thought in the completion budget).
Expect a DeepSeek dossier call to use ~1.5-3x the completion tokens
of a gpt-5-mini call for the same job, so the blended per-dossier
cost on DeepSeek will likely land in the same ballpark as gpt-5-mini
rather than being dramatically cheaper.

DeepSeek offers a ~50% off-peak discount (roughly 16:30–00:30 UTC);
the cost calculator does not currently model it, so actual bills may
be lower than the `cost_usd` column reports during off-peak.

## Architecture

One new module: [`src/vacancysoft/intelligence/providers.py`](../src/vacancysoft/intelligence/providers.py).

- `LLMProvider` — enum with `OPENAI` and `DEEPSEEK` members.
- `call_llm(...)` — provider-agnostic entry point. Returns the same
  dict shape as `client.call_chat`. Delegates to `client.call_chat`
  for OpenAI (so the original retry / web-search / reasoning-effort
  code is reused unchanged) and to `_call_deepseek` for DeepSeek.
- `_call_deepseek(...)` — its own retry loop + AsyncOpenAI client
  with `base_url="https://api.deepseek.com"`. Captures the
  reasoner's `reasoning_content` field (chain-of-thought) for
  diagnostics when present.

`client.py` is untouched. `dossier.py` and `campaign.py` import
`call_llm` and `LLMProvider` from `providers.py` and select the
provider at call time based on the config toggle.

## Testing the switch

The provider layer has no automated integration test (live calls
hit real APIs and cost money). To smoke-test after flipping:

1. `vacancysoft pipeline discover-demo` (or let the worker queue a
   real job).
2. From the UI, queue a lead: click "Queue campaign" on the leads
   page.
3. When the dossier / campaign row lands, check the `model_used`
   column in the database:
   ```sql
   SELECT model_used, cost_usd, tokens_used
   FROM intelligence_dossiers
   ORDER BY created_at DESC LIMIT 3;
   ```
   Expect `deepseek-reasoner` in `model_used` for the dossier row
   (or whichever the config says), and `gpt-4o` / `gpt-5.2` for the
   hiring-manager portion recorded in `call_breakdown`.
4. If the run fails with `DEEPSEEK_API_KEY is not set`, the key is
   missing from `.env` or the env-loading step in the worker isn't
   picking it up.

## Troubleshooting

**`RuntimeError: DEEPSEEK_API_KEY is not set`** — add it to `.env`
(see `.env.example`). The key is only checked lazily on first
DeepSeek call, so the worker starts fine without it and only fails
when a real dossier / campaign run is kicked off.

**Empty emails on DeepSeek** — the reasoner sometimes burns the
full `max_tokens` budget on internal reasoning. The fallback
(`deepseek-chat`) should catch this. If you still see empty output,
bump `max_tokens` in `[intelligence]` (current default 16000) or
drop to `deepseek-chat` as the primary by setting
`campaign_model_deepseek = "deepseek-chat"`.

**Worse dossier quality on DeepSeek** — most likely the missing
web-search context (see Caveat 1). Either accept it for the
cost saving or keep `use_deepseek_for_dossier = false`.

**Cost report shows `DEFAULT_PRICE`** — a new DeepSeek model ID
isn't in `PRICING`. Add it to
[`pricing.py`](../src/vacancysoft/intelligence/pricing.py) and
restart.

---

## SerpApi HM path — cheaper hiring-manager lookup

The hiring-manager step of the dossier (Call 2 in the analysis above)
is the single most expensive line in per-lead spend — on a typical
run it's ~72% of the dossier cost, because OpenAI is paid to both
search the web AND reason over the snippets. The SerpApi route
separates those:

- **SerpApi** runs the LinkedIn `site:linkedin.com/in` searches
  directly (~$0.015 per query on the Production plan).
- **gpt-4o-mini** reads only the result titles/snippets (~1.2 k
  tokens, vs ~30 k on the OpenAI route) and extracts names /
  titles / confidence.

Observed per-lead numbers (measured against a real Vanquis test
lead, on both paths):

| Path | LLM prompt/compl tok | Latency | LLM $ | SerpApi $ | Total $ | HMs returned |
|---|---:|---:|---:|---:|---:|---:|
| OpenAI gpt-5.2 + web_search (default) | 35,000 / 2,000 | ~56 s | $0.064 | — | **$0.064** | 0 |
| SerpApi **3** searches + gpt-4o-mini (this module's default) | ~1,800 / ~230 | ~11 s | $0.0004 | $0.045 | **$0.046** | *3 (inferred — we ran 5)* |
| SerpApi **5** searches + gpt-4o-mini | 2,866 / 226 | ~11 s | $0.0006 | $0.075 | **$0.076** | 3 real candidates including Head of Credit Risk @ high confidence |

**Saving at default (3 searches): ~$0.018/lead, ~28%.** Over 1,000
leads/month that's ~$18 — modest in absolute terms; the bigger wins
are the **5× latency drop** (HM step goes from the longest pipeline
step to one of the shortest) and, on this lead at least, **higher
recall than the OpenAI baseline** (3 real candidates where gpt-5.2
returned zero).

If recall matters more than cost, set
`hm_serpapi_max_searches = 5`. The extra ~$0.03/lead bought us
candidates from the 4th and 5th queries on Vanquis; whether that
holds in general is the thing you want to measure by running a
batch.

### Switch it on

```toml
[intelligence]
use_serpapi_hm_search = true   # (default: false)

# Override either of these if you want — defaults shown below:
hm_serpapi_max_searches = 5
hm_extraction_model     = "gpt-4o-mini"
```

Prerequisite: `SERPAPI_KEY` must be set in `.env`. It's already the
same key the Google Jobs adapter uses. If the key is missing or
SerpApi raises at runtime, `dossier.py` falls back to the OpenAI
web_search route automatically — no dossier is lost.

### Privacy model

Same as the OpenAI-only path: **named individuals only reach OpenAI**.
SerpApi returns public Google search results (LinkedIn snippets). We
send those snippets to `gpt-4o-mini` — still OpenAI. No third-party
LLM sees named people. The SerpApi route just replaces OpenAI's
*search* step with SerpApi's, keeping OpenAI as the *reasoning* step.

### Caveats

- **Quality is unverified at scale.** Observed to produce plausible
  candidates on limited testing; comparison to the baseline
  (Vanquis and a few others) has not been run. Run a batch and
  judge. The cost delta is certain; the quality delta is not.
- **Coverage depends on Google indexing LinkedIn profiles.** Some
  smaller firms have sparse LinkedIn coverage; on those, SerpApi
  returns little and gpt-4o-mini correctly returns an empty
  `hiring_managers: []`. Same failure mode as the old path — both
  depend on LinkedIn being public.
- **SerpApi charges per search.** On the Production plan (~$75/mo
  for 5,000 searches, ~$0.015/search) running 5 searches per lead
  is ~$0.075 per 1,000 leads. On the Developer plan (5,000/mo for
  $50) it's ~$0.050. `SERPAPI_COST_PER_SEARCH` env var overrides
  the default $0.015 figure used by the cost-report CLI if your
  plan differs.
- **Zero `hiring_managers` result (ticket 14) may still bite.**
  This change addresses per-lead cost, not zero-result failures.
  If the filter at
  [`dossier.py::_KNOWN_FAKE_NAMES`](../src/vacancysoft/intelligence/dossier.py)
  is the real problem, it applies to both paths and this doesn't
  help.

### Architecture

The SerpApi path is one module: [`hm_search_serpapi.py`](../src/vacancysoft/intelligence/hm_search_serpapi.py).
It exposes a single async function `run_hm_search_via_serpapi(...)`
that returns the same dict shape as `client.call_chat`, so
`dossier.py` substitutes one for the other at call time without
branching downstream. The `call_breakdown[hm]` entry on the
`IntelligenceDossier` row picks up extra keys on the SerpApi path
(`serpapi_searches`, `serpapi_cost_usd`, `llm_cost_usd`) for
cost-attribution analysis.

### Reverting

Two ways:

```toml
use_serpapi_hm_search = false   # one-line flip; instant
```

or `git revert` the commit that introduced the module — see
[`git log --oneline -- src/vacancysoft/intelligence/hm_search_serpapi.py`](#).
