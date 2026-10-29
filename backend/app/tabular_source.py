"""
Reading tabular import files.

CSV encoding and XLSX sheet selection are handled here so both importers
read files the same way. Column policy stays with each importer, because the
planned-activity schedule and a progress report describe their columns
differently.
"""

from pathlib import Path
from typing import Any

import pandas as pd


SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


def is_empty(value: Any) -> bool:
    """Treat None, whitespace-only strings and pandas missing values as absent."""
    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def clean_cell(value: Any) -> Any:
    """Convert pandas missing values to None without changing real values."""
    return None if is_empty(value) else value


def load_raw_table(
    path: Path,
    sheet_name: str | int,
) -> pd.DataFrame:
    """Read the file without treating its first row as column names."""
    if path.suffix.lower() == ".csv":
        return pd.read_csv(
            path,
            header=None,
            dtype=object,
            keep_default_na=False,
            skip_blank_lines=False,
            encoding="utf-8-sig",
            engine="python",
            on_bad_lines="error",
        )

    return pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=None,
        dtype=object,
        keep_default_na=False,
        engine="openpyxl",
    )


def normalize_headers(raw_headers: list[Any]) -> list[str]:
    """Lowercase and strip header names for case-insensitive matching."""
    return [str(header).strip().lower() for header in raw_headers]
