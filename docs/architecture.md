# Architecture

This repository is being rebuilt as a coverage-first scraping system.

## Core stages

1. discovery
2. raw persistence
3. enrichment
4. classification
5. scoring
6. dedupe
7. review
8. export
9. source health monitoring

## Principles

- discover aggressively, judge later
- store partial records immediately
- degrade gracefully when sources break
- isolate source failures per run
- make exports terminal artefacts rather than system of record
- preserve legacy taxonomy for downstream serving
