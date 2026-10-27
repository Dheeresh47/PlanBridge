
from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from typing import Any


REQUIRED_COLUMNS = {"external_id", "name"}
OPTIONAL_COLUMNS = {
    "description",
    "planned_start",
    "planned_finish",
    "planned_progress",
}


@dataclass
class RowError:
    row_number: int
    field: str
    message: str


@dataclass
class ValidatedActivity:
    row_number: int
    external_id: str
    name: str
    description: str | None = None
    planned_start: date | None = None
    planned_finish: date | None = None
    planned_progress: float | None = None


@dataclass
class ValidationResult:
    valid_rows: list[ValidatedActivity] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    skipped_empty_rows: int = 0


def _is_empty(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and not value.strip()
    )


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if not isinstance(value, str):
        raise ValueError("Expected a date in YYYY-MM-DD format.")

    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            "Expected a valid date in YYYY-MM-DD format."
        ) from exc


def _parse_progress(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Progress must be a number from 0 to 100.")

    try:
        progress = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Progress must be a number from 0 to 100."
        ) from exc

    if not isfinite(progress):
        raise ValueError("Progress must be a finite number from 0 to 100.")

    if not 0 <= progress <= 100:
        raise ValueError("Progress must be between 0 and 100.")

    return progress


def validate_planned_activity_rows(
    rows: list[dict[str, Any]],
) -> ValidationResult:
    """Validate planned activities without writing to the database."""
    result = ValidationResult()
    parsed_rows: list[ValidatedActivity] = []
    external_id_rows: dict[str, list[int]] = {}

    for row_number, row in enumerate(rows, start=2):
        # An empty dictionary represents a completely blank row.
        if not row:
            result.skipped_empty_rows += 1
            continue

        # Skip rows with no cell content only when they do not contain
        # either required column. Empty required fields are validation errors.
        if (
            not any(not _is_empty(value) for value in row.values())
            and not any(column in row for column in REQUIRED_COLUMNS)
        ):
            result.skipped_empty_rows += 1
            continue

        row_errors: list[RowError] = []

        external_id_value = row.get("external_id")
        name_value = row.get("name")

        external_id = (
            str(external_id_value).strip()
            if not _is_empty(external_id_value)
            else ""
        )
        name = (
            str(name_value).strip()
            if not _is_empty(name_value)
            else ""
        )

        if not external_id:
            row_errors.append(
                RowError(
                    row_number,
                    "external_id",
                    "This field is required.",
                )
            )

        if not name:
            row_errors.append(
                RowError(
                    row_number,
                    "name",
                    "This field is required.",
                )
            )

        parsed_dates: dict[str, date | None] = {}

        for column in ("planned_start", "planned_finish"):
            value = row.get(column)

            if _is_empty(value):
                parsed_dates[column] = None
                continue

            try:
                parsed_dates[column] = _parse_date(value)
            except ValueError as exc:
                row_errors.append(
                    RowError(row_number, column, str(exc))
                )
                parsed_dates[column] = None

        progress_value = row.get("planned_progress")
        planned_progress = None

        if not _is_empty(progress_value):
            try:
                planned_progress = _parse_progress(progress_value)
            except ValueError as exc:
                row_errors.append(
                    RowError(
                        row_number,
                        "planned_progress",
                        str(exc),
                    )
                )

        start = parsed_dates["planned_start"]
        finish = parsed_dates["planned_finish"]

        if start is not None and finish is not None and start > finish:
            row_errors.append(
                RowError(
                    row_number,
                    "planned_finish",
                    "Finish date must be on or after the start date.",
                )
            )

        if external_id:
            external_id_rows.setdefault(external_id, []).append(row_number)

        if not row_errors:
            description_value = row.get("description")

            parsed_rows.append(
                ValidatedActivity(
                    row_number=row_number,
                    external_id=external_id,
                    name=name,
                    description=(
                        None
                        if _is_empty(description_value)
                        else str(description_value).strip()
                    ),
                    planned_start=start,
                    planned_finish=finish,
                    planned_progress=planned_progress,
                )
            )

        result.errors.extend(row_errors)

    # Every row involved in a duplicate external_id is invalid.
    duplicate_ids = {
        external_id: row_numbers
        for external_id, row_numbers in external_id_rows.items()
        if len(row_numbers) > 1
    }

    for external_id, row_numbers in duplicate_ids.items():
        row_list = ", ".join(str(number) for number in row_numbers)

        for row_number in row_numbers:
            result.errors.append(
                RowError(
                    row_number,
                    "external_id",
                    f"Duplicate external_id '{external_id}' "
                    f"appears in rows {row_list}.",
                )
            )

    duplicate_row_numbers = {
        row_number
        for row_numbers in duplicate_ids.values()
        for row_number in row_numbers
    }

    result.valid_rows = [
        activity
        for activity in parsed_rows
        if activity.row_number not in duplicate_row_numbers
    ]

    result.errors.sort(
        key=lambda error: (error.row_number, error.field)
    )

    return result
