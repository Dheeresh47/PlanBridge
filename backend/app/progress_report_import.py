"""
Importing progress reports.

Importing a report records its text and provenance. It does not extract
progress events, match activities or change any schedule data: per the
contract, an imported statement is not an approved schedule update.

Validated optional observations are returned on the result so the later
extraction step can use them, but they are not persisted here.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.import_validation import RowError
from app.models import ProgressReport
from app.progress_report_reader import ParsedReportFile
from app.progress_report_validation import (
    validate_progress_report_rows,
)


@dataclass
class ImportedProgressReport:
    """A persisted report and the observations its source row supplied."""

    report_id: UUID
    row_number: int
    reported_activity_id: str | None = None
    reported_status: str | None = None
    reported_progress: float | None = None
    reported_date: date | None = None


@dataclass
class ProgressReportImportResult:
    """Summary of a progress-report import operation."""

    inserted_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    skipped_empty_count: int = 0
    errors: list[RowError] = field(default_factory=list)
    #: Optional logical fields the source file did not supply.
    unmapped_fields: list[str] = field(default_factory=list)
    reports: list[ImportedProgressReport] = field(default_factory=list)


def import_progress_reports(
    session: Session,
    project_id: UUID,
    parsed_file: ParsedReportFile,
) -> ProgressReportImportResult:
    """Persist validated progress reports, preserving their original text."""

    validation = validate_progress_report_rows(parsed_file.rows)

    result = ProgressReportImportResult(
        invalid_count=len(
            {error.row_number for error in validation.errors}
            - set(validation.duplicate_text_rows)
        ),
        duplicate_count=len(validation.duplicate_text_rows),
        skipped_empty_count=validation.skipped_empty_rows,
        errors=list(validation.errors),
        unmapped_fields=list(parsed_file.unmapped_fields),
    )

    try:
        for row in validation.valid_rows:
            report = ProgressReport(
                project_id=project_id,
                original_text=row.text,
                source_filename=parsed_file.source_filename,
                source_format=parsed_file.source_format,
                source_row=str(row.row_number),
            )
            session.add(report)

            # Flush to obtain the identifier without ending the transaction.
            session.flush()

            result.reports.append(
                ImportedProgressReport(
                    report_id=report.id,
                    row_number=row.row_number,
                    reported_activity_id=row.reported_activity_id,
                    reported_status=row.reported_status,
                    reported_progress=row.reported_progress,
                    reported_date=row.reported_date,
                )
            )
            result.inserted_count += 1

        if result.inserted_count > 0:
            session.commit()
        else:
            session.rollback()

    except Exception:
        session.rollback()
        raise

    return result
