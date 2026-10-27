
from datetime import date

from app.import_validation import validate_planned_activity_rows


def test_valid_activity_is_accepted():
    result = validate_planned_activity_rows(
        [
            {
                "external_id": "ACT-001",
                "name": "Design database",
                "description": "Prepare the schema",
                "planned_start": "2026-11-01",
                "planned_finish": "2026-11-10",
                "planned_progress": 25,
            }
        ]
    )

    assert len(result.valid_rows) == 1
    assert result.errors == []

    activity = result.valid_rows[0]
    assert activity.external_id == "ACT-001"
    assert activity.name == "Design database"
    assert activity.description == "Prepare the schema"
    assert activity.planned_start == date(2026, 11, 1)
    assert activity.planned_finish == date(2026, 11, 10)
    assert activity.planned_progress == 25.0


def test_missing_required_fields_are_rejected():
    result = validate_planned_activity_rows(
        [{"external_id": "", "name": ""}]
    )

    assert result.valid_rows == []
    assert {
        (error.field, error.message) for error in result.errors
    } == {
        ("external_id", "This field is required."),
        ("name", "This field is required."),
    }


def test_invalid_progress_is_rejected():
    result = validate_planned_activity_rows(
        [
            {
                "external_id": "ACT-002",
                "name": "Testing",
                "planned_progress": 120,
            }
        ]
    )

    assert result.valid_rows == []
    assert any(
        error.field == "planned_progress"
        and "between 0 and 100" in error.message
        for error in result.errors
    )


def test_non_finite_progress_is_rejected():
    for value in (float("nan"), float("inf"), float("-inf")):
        result = validate_planned_activity_rows(
            [
                {
                    "external_id": "ACT-NAN",
                    "name": "Testing",
                    "planned_progress": value,
                }
            ]
        )

        assert result.valid_rows == []
        assert any(
            error.field == "planned_progress"
            for error in result.errors
        )


def test_invalid_date_is_rejected():
    result = validate_planned_activity_rows(
        [
            {
                "external_id": "ACT-003",
                "name": "Deployment",
                "planned_start": "not-a-date",
            }
        ]
    )

    assert result.valid_rows == []
    assert any(
        error.field == "planned_start"
        for error in result.errors
    )


def test_finish_date_before_start_date_is_rejected():
    result = validate_planned_activity_rows(
        [
            {
                "external_id": "ACT-004",
                "name": "Review",
                "planned_start": "2026-11-10",
                "planned_finish": "2026-11-01",
            }
        ]
    )

    assert result.valid_rows == []
    assert any(
        error.field == "planned_finish"
        for error in result.errors
    )


def test_all_rows_with_duplicate_ids_are_rejected():
    result = validate_planned_activity_rows(
        [
            {"external_id": "ACT-005", "name": "First task"},
            {"external_id": "ACT-005", "name": "Second task"},
        ]
    )

    assert result.valid_rows == []

    duplicate_errors = [
        error
        for error in result.errors
        if error.field == "external_id"
        and "Duplicate external_id" in error.message
    ]

    assert len(duplicate_errors) == 2


def test_empty_rows_are_counted_and_skipped():
    result = validate_planned_activity_rows(
        [
            {"external_id": "ACT-006", "name": "Planning"},
            {},
        ]
    )

    assert len(result.valid_rows) == 1
    assert result.skipped_empty_rows == 1
    assert result.errors == []


def test_progress_boundaries_are_valid():
    result = validate_planned_activity_rows(
        [
            {
                "external_id": "ACT-007",
                "name": "Start",
                "planned_progress": 0,
            },
            {
                "external_id": "ACT-008",
                "name": "Finish",
                "planned_progress": 100,
            },
        ]
    )

    assert len(result.valid_rows) == 2
    assert result.errors == []


def test_empty_required_values_are_not_skipped():
    result = validate_planned_activity_rows(
        [
            {"external_id": "", "name": "   "},
        ]
    )

    assert result.skipped_empty_rows == 0
    assert result.valid_rows == []
    assert {error.field for error in result.errors} == {
        "external_id",
        "name",
    }


def test_optional_fields_can_be_missing():
    result = validate_planned_activity_rows(
        [{"external_id": "ACT-009", "name": "Documentation"}]
    )

    assert len(result.valid_rows) == 1
    assert result.errors == []

    activity = result.valid_rows[0]
    assert activity.description is None
    assert activity.planned_start is None
    assert activity.planned_finish is None
    assert activity.planned_progress is None
