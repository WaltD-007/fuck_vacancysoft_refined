# Taxonomy and segmentation

The original lead-serving taxonomy is preserved as a first-class classification layer.

## Why this exists

The redesign introduces scoring, enrichment and review, but that must not destroy the existing lead segmentation used for serving users.

## Design

- taxonomy version is stored on each classification result
- primary taxonomy key is stored explicitly
- secondary taxonomy keys are stored explicitly
- title-based and future description-based matches can both contribute
- exports can segment by primary taxonomy key exactly as before
- future taxonomies can be added without losing the legacy mapping

## Practical effect

A job can be:
- relevant overall
- low confidence overall
- still tagged to a legacy segment like risk, quant or compliance

That means you can continue serving leads by the original buckets while modernising the rest of the pipeline.
