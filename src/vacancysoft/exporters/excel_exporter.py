from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from sqlalchemy.orm import Session

from vacancysoft.exporters.profiles import resolve_profile_query, resolve_segment_query
from vacancysoft.exporters.serialisers import EXPORT_COLUMNS, row_to_dict
from vacancysoft.exporters.views import fetch_rows, load_exporter_config


def _write_sheet(workbook: Workbook, title: str, rows: list[dict]) -> None:
    worksheet = workbook.active
    worksheet.title = title
    worksheet.append(EXPORT_COLUMNS)
    for row in rows:
        worksheet.append([row.get(column) for column in EXPORT_COLUMNS])


def export_profile_to_excel(session: Session, profile_name: str, output_path: str | Path, limit: int = 100) -> Path:
    config = load_exporter_config()
    stmt = resolve_profile_query(profile_name, config)
    rows = [row_to_dict(row) for row in fetch_rows(session, stmt, limit=limit)]
    workbook = Workbook()
    sheet_name = config.get("profiles", {}).get(profile_name, {}).get("excel_sheet_name", profile_name)
    _write_sheet(workbook, sheet_name, rows)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def export_segment_to_excel(session: Session, segment_name: str, output_path: str | Path, limit: int = 100) -> Path:
    config = load_exporter_config()
    stmt = resolve_segment_query(segment_name, config)
    rows = [row_to_dict(row) for row in fetch_rows(session, stmt, limit=limit)]
    workbook = Workbook()
    _write_sheet(workbook, f"segment_{segment_name}", rows)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output
