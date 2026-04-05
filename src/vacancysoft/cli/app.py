from __future__ import annotations

import typer

from vacancysoft.db.base import Base
from vacancysoft.db.engine import build_engine

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


@pipeline_app.command("discover")
def discover(all_sources: bool = typer.Option(False, "--all")) -> None:
    target = "all configured sources" if all_sources else "selected source set"
    typer.echo(f"Discovery stub for {target}")


@pipeline_app.command("enrich")
def enrich(pending: bool = typer.Option(True, "--pending/--all")) -> None:
    typer.echo(f"Enrichment stub. pending_only={pending}")


@pipeline_app.command("classify")
def classify(pending: bool = typer.Option(True, "--pending/--all")) -> None:
    typer.echo(f"Classification stub. pending_only={pending}")


@pipeline_app.command("export")
def export(profile: str = typer.Option("accepted_only_excel", "--profile")) -> None:
    typer.echo(f"Export stub for profile={profile}")


@export_app.command("taxonomy-preview")
def taxonomy_preview() -> None:
    typer.echo("Taxonomy-aware export preview stub")


if __name__ == "__main__":
    app()
