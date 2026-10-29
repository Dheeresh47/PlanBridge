"""
Reading progress-report CSV and XLSX files.

Progress reports come from varied sources, so this reader does not assume
fixed column names. The caller supplies a ColumnMapping naming the source
column for each logical field; DEFAULT_COLUMN_MAPPING documents the
defaults. Columns the mapping does not name are ignored rather than
rejected, which is the point of having a mapping.
"""

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from app.tabular_source import (
    SUPPORTED_EXTENSIONS,
    clean_cell,
    is_empty,
    load_raw_table,
    normalize_headers,
)


class ProgressReportFileError(ValueError):
    """Raised when a progress-report file cannot be read safely."""


@dataclass(frozen=True)
class ColumnMapping:
    """
    Source column names for each logical progress-report field.

    `text` is required: it carries the original report text. The remaining
    fields are optional observations. Setting one to None declares that the
    file does not supply it.
    """

    text: str = "text"
    reported_activity_id: str | None = "activity_id"
    reported_status: str | None = "status"
    reported_progress: str | None = "progress"
    reported_date: str | None = "date"


DEFAULT_COLUMN_MAPPING = ColumnMapping()

TEXT_FIELD = "text"
OPTIONAL_FIELDS = tuple(
    field.name for field in fields(ColumnMapping) if field.name != TEXT_FIELD
)


@dataclass
class ParsedReportFile:
    source_filename: str
    source_format: str
    headers: list[str]
    #: Logical field name -> the source column it was read from.
    mapped_columns: dict[str, str]
    #: Optional logical fields with no matching column in this file. Their
    #: values are unknown for every row, and the caller can report that.
    unmapped_fields: list[str]
    #: Rows in source order, keyed by logical field name. A blank row is an
    #: empty dict so row numbers stay accurate, beginning with row 2.
    rows: list[dict[str, Any]]


def read_progress_report_file(
    file_path: str | Path,
    mapping: ColumnMapping = DEFAULT_COLUMN_MAPPING,
    sheet_name: str | int = 0,
) -> ParsedReportFile:
    """
    Read and structurally validate a progress-report CSV or XLSX file.

    Rows are returned keyed by logical field name, so downstream validation
    does not depend on the source column names. This function does not
    validate individual field values and does not touch the database.
    """
    path = Path(file_path)

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ProgressReportFileError(
            "Unsupported file type. Upload a .csv or .xlsx file."
        )

    if not path.exists() or not path.is_file():
        raise ProgressReportFileError(
            "The specified file does not exist or is not a file."
        )

    if is_empty(mapping.text):
        raise ProgressReportFileError(
            "The column mapping must name the report text column."
        )

    try:
        table = load_raw_table(path, sheet_name)
    except Exception as exc:
        raise ProgressReportFileError(
            "Could not read the file. Check that it is a valid, "
            "readable CSV or XLSX file and that the selected sheet exists."
        ) from exc

    if table.empty or table.shape[1] == 0:
        raise ProgressReportFileError(
            "The file is empty or does not contain a header row."
        )

    raw_headers = [clean_cell(value) for value in table.iloc[0].tolist()]

    # Ignore trailing empty header cells, but not empty cells between headers.
    while raw_headers and is_empty(raw_headers[-1]):
        raw_headers.pop()

    if not raw_headers:
        raise ProgressReportFileError(
            "The file is empty or does not contain a header row."
        )

    if any(is_empty(header) for header in raw_headers):
        raise ProgressReportFileError(
            "The header row contains an empty column name."
        )

    headers = normalize_headers(raw_headers)

    duplicates = sorted(
        {header for header in headers if headers.count(header) > 1}
    )
    if duplicates:
        raise ProgressReportFileError(
            "Duplicate column names after normalization: "
            + ", ".join(duplicates)
            + "."
        )

    header_positions = {header: index for index, header in enumerate(headers)}

    text_column = str(mapping.text).strip().lower()

    if text_column not in header_positions:
        raise ProgressReportFileError(
            f"The mapped report text column '{mapping.text}' is not present "
            "in the file. Available columns are: "
            + ", ".join(headers)
            + "."
        )

    mapped_columns = {TEXT_FIELD: text_column}
    unmapped_fields: list[str] = []

    for field_name in OPTIONAL_FIELDS:
        requested = getattr(mapping, field_name)

        if is_empty(requested):
            unmapped_fields.append(field_name)
            continue

        column = str(requested).strip().lower()

        if column in header_positions:
            mapped_columns[field_name] = column
        else:
            # Reported as unmapped rather than raising, so a report that
            # simply lacks an optional column still imports. The caller can
            # surface this to distinguish it from a mapping mistake.
            unmapped_fields.append(field_name)

    field_positions = {
        field_name: header_positions[column]
        for field_name, column in mapped_columns.items()
    }

    rows: list[dict[str, Any]] = []

    for raw_row in table.iloc[1:].itertuples(index=False, name=None):
        values = list(raw_row[: len(headers)])

        if all(is_empty(value) for value in values):
            rows.append({})
            continue

        rows.append(
            {
                field_name: (
                    clean_cell(values[position])
                    if position < len(values)
                    else None
                )
                for field_name, position in field_positions.items()
            }
        )

    return ParsedReportFile(
        source_filename=path.name,
        source_format=path.suffix.lower().lstrip("."),
        headers=headers,
        mapped_columns=mapped_columns,
        unmapped_fields=unmapped_fields,
        rows=rows,
    )
