"""
Validating progress-report rows before import.

The original report text is the evidence a progress event is later derived
from, so it is preserved exactly as read. Only a stripped copy is used to
decide whether a row has any text at all.

Optional values are observations. A missing one stays unknown; it is never
filled in with a zero, a date or a status.
"""

from dataclasses import dataclass, field
from datetime import date

from app.import_validation import RowError
from app.value_parsing import (
    is_empty,
    normalize_status,
    parse_date,
    parse_progress,
)


@dataclass
class ValidatedProgressReport:
    row_number: int
    #: The source text, preserved without normalization or trimming.
    text: str
    reported_activity_id: str | None = None
    reported_status: str | None = None
    reported_progress: float | None = None
    reported_date: date | None = None


@dataclass
class ProgressReportValidationResult:
    valid_rows: list[ValidatedProgressReport] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    skipped_empty_rows: int = 0
    #: Rows whose report text repeats another row's in the same upload.
    duplicate_text_rows: list[int] = field(default_factory=list)


def _comparable_text(text: str) -> str:
    """Collapse whitespace and case for duplicate detection only."""
    return " ".join(text.strip().lower().split())


def validate_progress_report_rows(
    rows: list[dict[str, object]],
) -> ProgressReportValidationResult:
    """Validate progress-report rows without writing to the database."""
    result = ProgressReportValidationResult()
    text_rows: dict[str, list[int]] = {}

    for row_number, row in enumerate(rows, start=2):
        # The reader represents a wholly blank source row as an empty dict.
        # Any other row is a real record: if it carries no report text that
        # is a validation error, not a row to discard silently. A row may
        # look empty here while holding content in an unmapped column.
        if not row:
            result.skipped_empty_rows += 1
            continue

        row_errors: list[RowError] = []

        text_value = row.get("text")
        text = "" if is_empty(text_value) else str(text_value)

        if not text:
            row_errors.append(
                RowError(row_number, "text", "This field is required.")
            )

        activity_id_value = row.get("reported_activity_id")
        reported_activity_id = (
            None
            if is_empty(activity_id_value)
            else str(activity_id_value).strip()
        )

        reported_status = None
        status_value = row.get("reported_status")

        if not is_empty(status_value):
            try:
                reported_status = normalize_status(status_value)
            except ValueError as exc:
                row_errors.append(
                    RowError(row_number, "reported_status", str(exc))
                )

        reported_progress = None
        progress_value = row.get("reported_progress")

        if not is_empty(progress_value):
            try:
                reported_progress = parse_progress(progress_value)
            except ValueError as exc:
                row_errors.append(
                    RowError(row_number, "reported_progress", str(exc))
                )

        reported_date = None
        date_value = row.get("reported_date")

        if not is_empty(date_value):
            try:
                reported_date = parse_date(date_value)
            except ValueError as exc:
                row_errors.append(
                    RowError(row_number, "reported_date", str(exc))
                )

        if not row_errors:
            text_rows.setdefault(_comparable_text(text), []).append(
                row_number
            )

            result.valid_rows.append(
                ValidatedProgressReport(
                    row_number=row_number,
                    text=text,
                    reported_activity_id=reported_activity_id,
                    reported_status=reported_status,
                    reported_progress=reported_progress,
                    reported_date=reported_date,
                )
            )

        result.errors.extend(row_errors)

    # Repeated report text is flagged for review but still imported: a
    # report may legitimately restate a statement, and discarding one copy
    # would drop evidence. Deciding which occurrences matter is a review
    # concern, not an import one.
    for row_numbers in text_rows.values():
        if len(row_numbers) < 2:
            continue

        row_list = ", ".join(str(number) for number in row_numbers)

        for row_number in row_numbers:
            result.duplicate_text_rows.append(row_number)
            result.errors.append(
                RowError(
                    row_number,
                    "text",
                    "Report text is repeated in rows "
                    f"{row_list}; imported for review.",
                )
            )

    result.duplicate_text_rows.sort()
    result.errors.sort(key=lambda error: (error.row_number, error.field))

    return result
