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
from uuid import UUID

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


UPLOAD_SCOPE = "upload"
EXISTING_REPORT_SCOPE = "existing_report"


@dataclass
class DuplicateFlag:
    """
    A diagnostic that a row's report text repeats text seen elsewhere.

    A duplicate flag is not a validation error: it never prevents an import.
    `imported` states what actually happened to the row, so a flagged row
    that was stored can be told from one rejected for an unrelated error.
    """

    row_number: int
    scope: str
    message: str
    #: For same-upload groups: the deterministic reference occurrence.
    reference_row: int | None = None
    #: For matches against stored reports.
    existing_report_id: UUID | None = None
    existing_source_filename: str | None = None
    existing_source_row: str | None = None
    imported: bool = False


@dataclass
class ProgressReportValidationResult:
    valid_rows: list[ValidatedProgressReport] = field(default_factory=list)
    #: Blocking errors only. Duplicates are reported separately.
    errors: list[RowError] = field(default_factory=list)
    skipped_empty_rows: int = 0
    #: Same-upload duplicate diagnostics.
    duplicate_flags: list[DuplicateFlag] = field(default_factory=list)
    #: Normalized text for every row with nonblank text, including rows that
    #: failed validation, so comparison against stored reports can use it.
    comparable_text_by_row: dict[int, str] = field(default_factory=dict)


def comparable_text(text: str) -> str:
    """
    Collapse whitespace and case for duplicate detection only.

    Applied identically to uploaded rows and to stored reports so that
    same-upload and cross-upload detection cannot disagree.
    """
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

        # Duplicate detection considers every row with nonblank text, even
        # one rejected for another reason, so repeated evidence is visible
        # regardless of a row's other problems.
        if text:
            normalized = comparable_text(text)
            result.comparable_text_by_row[row_number] = normalized
            text_rows.setdefault(normalized, []).append(row_number)

        if not row_errors:
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

    # Repeated report text is flagged for review but never discarded: a
    # report may legitimately restate a statement, and dropping one copy
    # would lose evidence. Which occurrence matters is a review concern.
    for row_numbers in text_rows.values():
        if len(row_numbers) < 2:
            continue

        # The lowest source row is the reference occurrence, so the same
        # upload always produces the same diagnostics.
        reference_row = min(row_numbers)
        row_list = ", ".join(str(number) for number in sorted(row_numbers))

        for row_number in sorted(row_numbers):
            result.duplicate_flags.append(
                DuplicateFlag(
                    row_number=row_number,
                    scope=UPLOAD_SCOPE,
                    reference_row=reference_row,
                    message=(
                        f"Report text is repeated in rows {row_list} of this "
                        f"upload; first occurrence is row {reference_row}."
                    ),
                )
            )

    result.duplicate_flags.sort(
        key=lambda flag: (flag.row_number, flag.scope)
    )
    result.errors.sort(key=lambda error: (error.row_number, error.field))

    return result
