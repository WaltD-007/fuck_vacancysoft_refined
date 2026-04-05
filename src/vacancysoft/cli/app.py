from __future__ import annotations

import typer

app = typer.Typer(help="Coverage-first job scraping pipeline")
pipeline_app = typer.Typer(help="Pipeline commands")
app.add_typer(pipeline_app, name="pipeline")


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


if __name__ == "__main__":
    app()
