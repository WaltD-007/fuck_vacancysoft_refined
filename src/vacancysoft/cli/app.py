from __future__ import annotations

import typer
from sqlalchemy import select

from vacancysoft.adapters.base import DiscoveredJobRecord
from vacancysoft.db.base import Base
from vacancysoft.db.engine import build_engine
from vacancysoft.db.models import ClassificationResult, EnrichedJob, RawJob, ScoreResult, Source, SourceRun
from vacancysoft.db.session import SessionLocal
from vacancysoft.exporters.views import (
    accepted_only_query,
    accepted_plus_review_query,
    client_segment_query,
    fetch_rows,
    grouped_by_taxonomy_query,
    load_exporter_config,
)
from vacancysoft.pipelines.classification_persistence import classify_enriched_jobs
from vacancysoft.pipelines.enrichment_persistence import enrich_raw_jobs
from vacancysoft.pipelines.maintenance import cleanup_orphaned_classification_results
from vacancysoft.pipelines.persistence import persist_discovery_batch
from vacancysoft.pipelines.scoring_persistence import score_enriched_jobs
from vacancysoft.source_registry.seed_loader import seed_sources_from_yaml

app = typer.Typer(help="Coverage-first job scraping pipeline")
pipeline_app = typer.Typer(help="Pipeline commands")
export_app = typer.Typer(help="Export helpers")
db_app = typer.Typer(help="Database helpers")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(export_app, name="export")
app.add_typer(db_app, name="db")


@db_app.command("init")
def init_db() -> None:
    engine = build_engine()
    Base.metadata.create_all(bind=engine)
    typer.echo("Database schema initialised")


@db_app.command("seed-sources")
def seed_sources(config_path: str = typer.Option("configs/seeds/employers.yaml", "--config-path")) -> None:
    with SessionLocal() as session:
        created, updated = seed_sources_from_yaml(session, config_path)
    typer.echo(f"Seeded sources. created={created} updated={updated}")


@db_app.command("stats")
def db_stats() -> None:
    with SessionLocal() as session:
        source_count = len(list(session.execute(select(Source)).scalars()))
        run_count = len(list(session.execute(select(SourceRun)).scalars()))
        raw_job_count = len(list(session.execute(select(RawJob)).scalars()))
        enriched_job_count = len(list(session.execute(select(EnrichedJob)).scalars()))
        classification_count = len(list(session.execute(select(ClassificationResult)).scalars()))
        score_count = len(list(session.execute(select(ScoreResult)).scalars()))
    typer.echo(
        f"sources={source_count} source_runs={run_count} raw_jobs={raw_job_count} enriched_jobs={enriched_job_count} classification_results={classification_count} score_results={score_count}"
    )


@db_app.command("cleanup-classifications")
def cleanup_classifications() -> None:
    with SessionLocal() as session:
        removed = cleanup_orphaned_classification_results(session)
    typer.echo(f"Removed orphaned classification results: {removed}")


@pipeline_app.command("discover")
def discover(all_sources: bool = typer.Option(False, "--all")) -> None:
    target = "all configured sources" if all_sources else "selected source set"
    typer.echo(f"Discovery stub for {target}")


@pipeline_app.command("discover-demo")
def discover_demo(source_key: str | None = typer.Option(None, "--source-key")) -> None:
    with SessionLocal() as session:
        if source_key:
            source = session.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()
        else:
            source = session.execute(select(Source).where(Source.active.is_(True)).limit(1)).scalar_one_or_none()

        if source is None:
            raise typer.BadParameter("No source found. Run 'vacancysoft db seed-sources' first.")

        sample_jobs = [
            DiscoveredJobRecord(
                external_job_id=f"demo-{source.source_key}-001",
                title_raw=f"Senior Risk Manager at {source.employer_name}",
                location_raw="London, UK",
                posted_at_raw="2026-04-05",
                summary_raw="Demo discovery record persisted through the new pipeline.",
                discovered_url=f"{source.base_url.rstrip('/')}/jobs/demo-001",
                apply_url=f"{source.base_url.rstrip('/')}/jobs/demo-001/apply",
                listing_payload={"demo": True, "source_key": source.source_key},
                completeness_score=0.82,
                extraction_confidence=0.88,
                provenance={"mode": "demo", "source_key": source.source_key},
            )
        ]
        source_run, count = persist_discovery_batch(session=session, source=source, records=sample_jobs, trigger="manual")
        source_run_id = source_run.id
    typer.echo(f"Demo discovery persisted. source_run_id={source_run_id} raw_jobs={count}")


@pipeline_app.command("enrich")
def enrich(pending: bool = typer.Option(True, "--pending/--all")) -> None:
    typer.echo(f"Enrichment stub. pending_only={pending}")


@pipeline_app.command("enrich-demo")
def enrich_demo(limit: int = typer.Option(10, "--limit")) -> None:
    with SessionLocal() as session:
        count = enrich_raw_jobs(session, limit=limit)
    typer.echo(f"Demo enrichment persisted. enriched_jobs={count}")


@pipeline_app.command("classify")
def classify(pending: bool = typer.Option(True, "--pending/--all")) -> None:
    typer.echo(f"Classification stub. pending_only={pending}")


@pipeline_app.command("classify-demo")
def classify_demo(limit: int = typer.Option(10, "--limit")) -> None:
    with SessionLocal() as session:
        count = classify_enriched_jobs(session, limit=limit)
    typer.echo(f"Demo classification persisted. classification_results={count}")


@pipeline_app.command("score-demo")
def score_demo(limit: int = typer.Option(10, "--limit")) -> None:
    with SessionLocal() as session:
        count = score_enriched_jobs(session, limit=limit)
    typer.echo(f"Demo scoring persisted. score_results={count}")


@pipeline_app.command("export")
def export(profile: str = typer.Option("accepted_only_excel", "--profile")) -> None:
    typer.echo(f"Export stub for profile={profile}")


@export_app.command("taxonomy-preview")
def taxonomy_preview() -> None:
    with SessionLocal() as session:
        rows = list(
            session.execute(
                select(
                    EnrichedJob.title,
                    ClassificationResult.primary_taxonomy_key,
                    ClassificationResult.taxonomy_version,
                    ClassificationResult.decision,
                    ScoreResult.export_decision,
                    ScoreResult.export_eligibility_score,
                )
                .join(ClassificationResult, ClassificationResult.enriched_job_id == EnrichedJob.id)
                .join(ScoreResult, ScoreResult.enriched_job_id == EnrichedJob.id, isouter=True)
                .order_by(ClassificationResult.created_at.desc())
                .limit(10)
            )
        )
    if not rows:
        typer.echo("No classification results found")
        return
    for row in rows:
        typer.echo(
            f"title={row.title} taxonomy={row.primary_taxonomy_key} version={row.taxonomy_version} classification={row.decision} export={row.export_decision} score={row.export_eligibility_score}"
        )


@export_app.command("preview-view")
def preview_view(view_name: str = typer.Option(..., "--view"), limit: int = typer.Option(20, "--limit")) -> None:
    with SessionLocal() as session:
        if view_name == "accepted_only":
            rows = fetch_rows(session, accepted_only_query(), limit=limit)
        elif view_name == "accepted_plus_review":
            rows = fetch_rows(session, accepted_plus_review_query(), limit=limit)
        elif view_name == "grouped_by_taxonomy":
            rows = fetch_rows(session, grouped_by_taxonomy_query(), limit=limit)
        else:
            raise typer.BadParameter("Unknown view. Use accepted_only, accepted_plus_review, or grouped_by_taxonomy.")

    if not rows:
        typer.echo("No rows found")
        return
    for row in rows:
        typer.echo(str(row))


@export_app.command("preview-segment")
def preview_segment(segment_name: str = typer.Option(..., "--segment"), limit: int = typer.Option(20, "--limit")) -> None:
    config = load_exporter_config()
    with SessionLocal() as session:
        rows = fetch_rows(session, client_segment_query(segment_name, config), limit=limit)
    if not rows:
        typer.echo("No rows found")
        return
    for row in rows:
        typer.echo(str(row))


if __name__ == "__main__":
    app()
