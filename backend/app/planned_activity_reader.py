
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.import_validation import (
    REQUIRED_COLUMNS,
    OPTIONAL_COLUMNS,
)
from app.tabular_source import (
    SUPPORTED_EXTENSIONS,
    clean_cell as _clean_cell,
    is_empty as _is_empty,
    load_raw_table as _load_raw_table,
    normalize_headers,
)


ALLOWED_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS


class PlannedActivityFileError(ValueError):
    """Raised when a planned-activity file cannot be read safely."""


@dataclass
class ParsedActivityFile:
    source_filename: str
    source_format: str
    headers: list[str]
    rows: list[dict[str, Any]]


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

    headers = normalize_headers(raw_headers)

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
