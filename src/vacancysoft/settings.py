from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class AppSettings(BaseModel):
    project_root: Path = Path.cwd()
    database_url: str = "sqlite:///./.data/vacancysoft.db"
    raw_payload_dir: Path = Path("artifacts/raw")
    log_level: str = "INFO"


def get_settings() -> AppSettings:
    return AppSettings()
