
from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models import PlannedActivity
from app.planned_activity_import import import_planned_activities
from app.planned_activity_reader import ParsedActivityFile


def make_parsed_file(rows):
    return ParsedActivityFile(
        source_filename="activities.csv",
        source_format="csv",
        headers=["external_id", "name"],
        rows=rows,
    )


def make_session(existing_activities=None):
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value.all.return_value = (
        existing_activities or []
    )
    return session


def test_import_inserts_valid_activity_and_preserves_source_metadata():
    session = make_session()
    project_id = uuid4()

    result = import_planned_activities(
        session,
        project_id,
        make_parsed_file(
            [
                {
                    "external_id": "ACT-001",
                    "name": "Design database",
                    "planned_start": "2026-11-01",
                    "planned_finish": "2026-11-10",
                    "planned_progress": 25,
                }
            ]
        ),
    )

    assert result.inserted_count == 1
    assert result.invalid_count == 0
    assert result.conflict_count == 0
    session.add.assert_called_once()

    inserted = session.add.call_args.args[0]
    assert isinstance(inserted, PlannedActivity)
    assert inserted.project_id == project_id
    assert inserted.external_id == "ACT-001"
    assert inserted.name == "Design database"
    assert inserted.planned_start == date(2026, 11, 1)
    assert inserted.source_metadata == {
        "source_filename": "activities.csv",
        "source_format": "csv",
        "source_row": 2,
    }
    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_import_does_not_overwrite_existing_activity():
    existing = MagicMock()
    existing.external_id = "ACT-001"
    session = make_session([existing])

    result = import_planned_activities(
        session,
        uuid4(),
        make_parsed_file(
            [{"external_id": "ACT-001", "name": "Updated name"}]
        ),
    )

    assert result.inserted_count == 0
    assert result.conflict_count == 1
    assert any(
        "already exists" in error.message for error in result.errors
    )
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_called_once()


def test_import_flags_all_rows_with_duplicate_external_ids():
    session = make_session()

    result = import_planned_activities(
        session,
        uuid4(),
        make_parsed_file(
            [
                {"external_id": "ACT-001", "name": "First task"},
                {"external_id": "ACT-001", "name": "Second task"},
            ]
        ),
    )

    assert result.inserted_count == 0
    assert result.conflict_count == 2
    duplicate_errors = [
        error
        for error in result.errors
        if "Duplicate external_id" in error.message
    ]
    assert {error.row_number for error in duplicate_errors} == {2, 3}
    session.add.assert_not_called()


def test_import_counts_invalid_and_empty_rows():
    session = make_session()

    result = import_planned_activities(
        session,
        uuid4(),
        make_parsed_file(
            [
                {"external_id": "", "name": "Missing ID"},
                {},
            ]
        ),
    )

    assert result.inserted_count == 0
    assert result.invalid_count == 1
    assert result.skipped_empty_count == 1
    session.add.assert_not_called()


def test_import_rolls_back_and_reraises_database_errors():
    session = make_session()
    session.commit.side_effect = RuntimeError("Database write failed")

    with pytest.raises(RuntimeError, match="Database write failed"):
        import_planned_activities(
            session,
            uuid4(),
            make_parsed_file(
                [{"external_id": "ACT-001", "name": "New task"}]
            ),
        )

    session.rollback.assert_called_once()


def test_duplicate_id_row_with_validation_error_counts_as_conflict_only():
    session = make_session()

    result = import_planned_activities(
        session,
        uuid4(),
        make_parsed_file(
            [
                {"external_id": "ACT-001", "name": "First task"},
                {
                    "external_id": "ACT-001",
                    "name": "",
                    "planned_progress": 150,
                },
            ]
        ),
    )

    assert result.conflict_count == 2
    assert result.invalid_count == 0
    assert result.inserted_count == 0
    assert session.add.call_count == 0
