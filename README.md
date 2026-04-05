# fuck_vacancysoft_refined

Coverage-first redesign of the Vacancysoft scraping pipeline.

## Design goals

- maximise source coverage
- tolerate partial discovery records
- defer enrichment and validation to later stages
- isolate source failures
- preserve legacy taxonomy segmentation for serving users
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
alembic upgrade head
```

## CLI

```bash
vacancysoft db init
vacancysoft pipeline discover --all
vacancysoft pipeline classify --pending
vacancysoft pipeline export --profile accepted_only_excel
vacancysoft export taxonomy-preview
```
