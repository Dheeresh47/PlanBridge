
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.import_validation import (
    REQUIRED_COLUMNS,
    OPTIONAL_COLUMNS,
)


SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}
ALLOWED_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS


class PlannedActivityFileError(ValueError):
    """Raised when a planned-activity file cannot be read safely."""


@dataclass
class ParsedActivityFile:
    source_filename: str
    source_format: str
    headers: list[str]
    rows: list[dict[str, Any]]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _clean_cell(value: Any) -> Any:
    """Convert pandas missing values to None without changing real values."""
    return None if _is_empty(value) else value


def _load_raw_table(
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


def read_planned_activity_file(
    file_path: str | Path,
    sheet_name: str | int = 0,
) -> ParsedActivityFile:
    """
    Read and structurally validate a planned-activity CSV or XLSX file.

    Data rows remain in source order. Blank rows between records are
    retained as empty dictionaries so the existing row validator can
    preserve spreadsheet row numbers, beginning with row 2.

    This function does not validate individual field values and does not
    read from or write to the database.
    """
    path = Path(file_path)

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise PlannedActivityFileError(
            "Unsupported file type. Upload a .csv or .xlsx file."
        )

    if not path.exists() or not path.is_file():
        raise PlannedActivityFileError(
            "The specified file does not exist or is not a file."
        )

    try:
        table = _load_raw_table(path, sheet_name)
    except Exception as exc:
        raise PlannedActivityFileError(
            "Could not read the file. Check that it is a valid, "
            "readable CSV or XLSX file and that the selected sheet exists."
        ) from exc

    if table.empty or table.shape[1] == 0:
        raise PlannedActivityFileError(
            "The file is empty or does not contain a header row."
        )

    raw_headers = [_clean_cell(value) for value in table.iloc[0].tolist()]

    # Ignore trailing empty header cells, but not empty cells between headers.
    while raw_headers and _is_empty(raw_headers[-1]):
        raw_headers.pop()

    if not raw_headers or all(_is_empty(header) for header in raw_headers):
        raise PlannedActivityFileError(
            "The file is empty or does not contain a header row."
        )

    if any(_is_empty(header) for header in raw_headers):
        raise PlannedActivityFileError(
            "The header row contains an empty column name."
        )

    headers = [str(header).strip().lower() for header in raw_headers]

    duplicates = sorted(
        {
            header
            for header in headers
            if headers.count(header) > 1
        }
    )
    if duplicates:
        raise PlannedActivityFileError(
            "Duplicate column names after normalization: "
            + ", ".join(duplicates)
            + "."
        )

    missing = sorted(REQUIRED_COLUMNS - set(headers))
    if missing:
        raise PlannedActivityFileError(
            "Missing required column(s): " + ", ".join(missing) + "."
        )

    unexpected = sorted(set(headers) - ALLOWED_COLUMNS)
    if unexpected:
        raise PlannedActivityFileError(
            "Unsupported column(s): "
            + ", ".join(unexpected)
            + ". Allowed columns are: "
            + ", ".join(sorted(ALLOWED_COLUMNS))
            + "."
        )

    data_rows: list[dict[str, Any]] = []

    # Each row in the table after the header corresponds to a source row
    # starting at spreadsheet/CSV row 2. Keep blank rows as {} so the
    # existing validator's enumerate(..., start=2) remains accurate.
    for raw_row in table.iloc[1:].itertuples(index=False, name=None):
        values = list(raw_row[: len(headers)])

        if all(_is_empty(value) for value in values):
            data_rows.append({})
            continue

        row = {
            header: _clean_cell(values[index])
            if index < len(values)
            else None
            for index, header in enumerate(headers)
        }
        data_rows.append(row)

    return ParsedActivityFile(
        source_filename=path.name,
        source_format=path.suffix.lower().lstrip("."),
        headers=headers,
        rows=data_rows,
    )
