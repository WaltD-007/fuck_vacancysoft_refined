from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class AppSettings(BaseModel):
    project_root: Path = Path.cwd()
    database_url: str = "sqlite:///./.data/prospero.db"
    raw_payload_dir: Path = Path("artifacts/raw")
    log_level: str = "INFO"


def get_settings() -> AppSettings:
    try:
        with open("configs/app.toml", "rb") as f:
            cfg = tomllib.load(f)
        app_cfg = cfg.get("app", {})
        return AppSettings(
            database_url=app_cfg.get("database_url", "sqlite:///./.data/prospero.db"),
            log_level=app_cfg.get("log_level", "INFO"),
        )
    except Exception:
        return AppSettings()
