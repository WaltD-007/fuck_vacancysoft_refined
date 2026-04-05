# fuck_vacancysoft_refined

Coverage-first redesign of the Vacancysoft scraping pipeline.

## Design goals

- maximise source coverage
- tolerate partial discovery records
- defer enrichment and validation to later stages
- isolate source failures
- make broken sources observable and cheap to repair
- export from curated database views, not directly from scraper output

## Planned architecture

- discovery
- raw persistence
- enrichment
- classification
- scoring
- dedupe
- review
- export
- source health monitoring

## Local setup

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
```

## CLI

```bash
vacancysoft pipeline discover --all
vacancysoft pipeline enrich --pending
vacancysoft pipeline classify --pending
vacancysoft pipeline export --profile accepted_only_excel
```
