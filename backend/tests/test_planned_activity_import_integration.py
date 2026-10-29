"""
Database-backed tests for the planned-activity import service.

These exercise the real import workflow against PostgreSQL rather than a
mocked session, so the service's own queries, inserts, commits and rollbacks
are the behaviour under test. See conftest.py for the test-database guards.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models import PlannedActivity
from app.planned_activity_import import import_planned_activities
from app.planned_activity_reader import ParsedActivityFile


pytestmark = pytest.mark.integration


def make_parsed_file(rows, source_filename="activities.csv"):
    return ParsedActivityFile(
        source_filename=source_filename,
        source_format="csv",
        headers=[
            "external_id",
            "name",
            "planned_start",
            "planned_finish",
            "planned_progress",
        ],
        rows=rows,
    )


def stored_activities(session, project_id):
    """Re-read the activities of a project from the database."""
    session.expire_all()
    return (
        session.query(PlannedActivity)
        .filter(PlannedActivity.project_id == project_id)
        .order_by(PlannedActivity.external_id)
        .all()
    )


def test_import_persists_new_activities(db_session, project):
    result = import_planned_activities(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "external_id": "ACT-001",
                    "name": "Design database",
                    "planned_start": "2026-11-01",
                    "planned_finish": "2026-11-10",
                    "planned_progress": "25",
                },
                {
                    "external_id": "ACT-002",
                    "name": "Build importer",
                },
            ]
        ),
    )

    assert result.inserted_count == 2
    assert result.invalid_count == 0
    assert result.conflict_count == 0
    assert result.skipped_empty_count == 0
    assert result.errors == []

    activities = stored_activities(db_session, project.id)
    assert [activity.external_id for activity in activities] == [
        "ACT-001",
        "ACT-002",
    ]

    first, second = activities

    assert first.project_id == project.id
    assert first.name == "Design database"
    assert first.planned_start == date(2026, 11, 1)
    assert first.planned_finish == date(2026, 11, 10)
    assert first.planned_progress == Decimal("25.00")
    assert first.source_metadata == {
        "source_filename": "activities.csv",
        "source_format": "csv",
        "source_row": 2,
    }

    # Reported progress and status are never inferred at import time.
    assert first.current_progress is None
    assert first.status == "unknown"

    assert second.planned_start is None
    assert second.planned_finish is None
    assert second.planned_progress is None
    assert second.source_metadata["source_row"] == 3


def test_reimport_reports_conflict_and_leaves_existing_record_unchanged(
    db_session, project
):
    import_planned_activities(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "external_id": "ACT-001",
                    "name": "Design database",
                    "planned_start": "2026-11-01",
                    "planned_finish": "2026-11-10",
                },
                {"external_id": "ACT-002", "name": "Build importer"},
            ]
        ),
    )

    result = import_planned_activities(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "external_id": "ACT-001",
                    "name": "Overwritten name",
                    "planned_start": "2027-01-01",
                    "planned_finish": "2027-01-31",
                    "planned_progress": "99",
                },
                {"external_id": "ACT-002", "name": "Also overwritten"},
            ],
            source_filename="second-upload.csv",
        ),
    )

    assert result.inserted_count == 0
    assert result.conflict_count == 2
    assert result.invalid_count == 0
    assert len(result.errors) == 2
    assert all(
        "already exists" in error.message for error in result.errors
    )
    assert {error.row_number for error in result.errors} == {2, 3}

    activities = stored_activities(db_session, project.id)
    assert len(activities) == 2

    first, second = activities

    assert first.name == "Design database"
    assert first.planned_start == date(2026, 11, 1)
    assert first.planned_finish == date(2026, 11, 10)
    assert first.planned_progress is None
    assert first.source_metadata["source_filename"] == "activities.csv"
    assert second.name == "Build importer"


def test_import_is_scoped_to_target_project(db_session, project, project_b):
    parsed_file = make_parsed_file(
        [{"external_id": "ACT-001", "name": "Design database"}]
    )

    first_result = import_planned_activities(
        db_session, project.id, parsed_file
    )
    second_result = import_planned_activities(
        db_session, project_b.id, parsed_file
    )

    # The same external_id is a new activity in a different project.
    assert first_result.inserted_count == 1
    assert second_result.inserted_count == 1
    assert second_result.conflict_count == 0

    for owner in (project, project_b):
        activities = stored_activities(db_session, owner.id)
        assert len(activities) == 1
        assert activities[0].external_id == "ACT-001"
        assert activities[0].project_id == owner.id


def test_duplicate_external_ids_within_upload_insert_nothing(
    db_session, project
):
    result = import_planned_activities(
        db_session,
        project.id,
        make_parsed_file(
            [
                {"external_id": "ACT-001", "name": "First task"},
                {"external_id": "ACT-001", "name": "Second task"},
            ]
        ),
    )

    assert result.inserted_count == 0
    assert result.conflict_count == 2
    assert stored_activities(db_session, project.id) == []
