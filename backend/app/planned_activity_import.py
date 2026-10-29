from uuid import UUID

from sqlalchemy.orm import Session

from app.import_validation import validate_planned_activity_rows
from app.models import PlannedActivity
from app.planned_activity_reader import ParsedActivityFile
from dataclasses import dataclass, field

from app.import_validation import RowError


@dataclass
class PlannedActivityImportResult:
    """Summary of a planned-activity import operation."""

    inserted_count: int = 0
    invalid_count: int = 0
    conflict_count: int = 0
    skipped_empty_count: int = 0
    errors: list[RowError] = field(default_factory=list)


def import_planned_activities(
    session: Session,
    project_id: UUID,
    parsed_file: ParsedActivityFile,
) -> PlannedActivityImportResult:
    """Import validated activities without overwriting existing records."""

    validation = validate_planned_activity_rows(parsed_file.rows)

    result = PlannedActivityImportResult(
        skipped_empty_count=validation.skipped_empty_rows,
        errors=list(validation.errors),
    )

    # Count rows involved in duplicate external IDs within the uploaded file.
    duplicate_row_numbers = {
        error.row_number
        for error in validation.errors
        if error.field == "external_id"
        and error.message.startswith("Duplicate external_id ")
    }

    result.conflict_count = len(duplicate_row_numbers)

    # Count ordinary invalid rows once, excluding rows already classified
    # as duplicate-ID conflicts.
    invalid_row_numbers = {
        error.row_number for error in validation.errors
    } - duplicate_row_numbers

    result.invalid_count = len(invalid_row_numbers)

    try:
        # Only query activities that passed validation. Duplicate IDs within
        # the uploaded file have already been excluded by the validator.
        if validation.valid_rows:
            external_ids = [
                activity.external_id for activity in validation.valid_rows
            ]

            existing_activities = (
                session.query(PlannedActivity)
                .filter(
                    PlannedActivity.project_id == project_id,
                    PlannedActivity.external_id.in_(external_ids),
                )
                .all()
            )

            existing_external_ids = {
                activity.external_id for activity in existing_activities
            }

            for activity in validation.valid_rows:
                if activity.external_id in existing_external_ids:
                    result.conflict_count += 1
                    result.errors.append(
                        RowError(
                            row_number=activity.row_number,
                            field="external_id",
                            message=(
                                f"Activity '{activity.external_id}' already "
                                "exists in this project; existing record "
                                "was left unchanged."
                            ),
                        )
                    )
                    continue

                session.add(
                    PlannedActivity(
                        project_id=project_id,
                        external_id=activity.external_id,
                        name=activity.name,
                        description=activity.description,
                        planned_start=activity.planned_start,
                        planned_finish=activity.planned_finish,
                        planned_progress=activity.planned_progress,
                        source_metadata={
                            "source_filename": parsed_file.source_filename,
                            "source_format": parsed_file.source_format,
                            "source_row": activity.row_number,
                        },
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
