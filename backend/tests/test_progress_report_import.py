"""
Database-backed tests for the progress-report import service.

The mocked test at the end covers only the failure path, where no
identifier is needed. Everything else runs against planbridge_test.
"""

from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models import PlannedActivity, ProgressReport
from app.progress_report_import import import_progress_reports
from app.progress_report_reader import ParsedReportFile


def make_parsed_file(rows, unmapped_fields=None, source_filename="week-1.csv"):
    return ParsedReportFile(
        source_filename=source_filename,
        source_format="csv",
        headers=["text", "activity_id", "status", "progress", "date"],
        mapped_columns={"text": "text"},
        unmapped_fields=list(unmapped_fields or []),
        rows=rows,
    )


def stored_reports(session, project_id):
    session.expire_all()
    return (
        session.query(ProgressReport)
        .filter(ProgressReport.project_id == project_id)
        .order_by(ProgressReport.source_row)
        .all()
    )


@pytest.mark.integration
def test_import_persists_reports_with_original_text_and_provenance(
    db_session, project
):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "text": "  Poured the foundation.  ",
                    "reported_activity_id": "A-101",
                    "reported_status": "Completed",
                    "reported_progress": "100",
                    "reported_date": "2026-11-02",
                },
                {"text": "Framing under way"},
            ]
        ),
    )

    assert result.inserted_count == 2
    assert result.invalid_count == 0
    assert result.duplicate_count == 0
    assert result.skipped_empty_count == 0
    assert result.errors == []

    reports = stored_reports(db_session, project.id)
    assert len(reports) == 2

    first, second = reports

    # The original text is stored exactly as supplied.
    assert first.original_text == "  Poured the foundation.  "
    assert first.project_id == project.id
    assert first.source_filename == "week-1.csv"
    assert first.source_format == "csv"
    assert first.source_row == "2"
    assert first.imported_at is not None
    assert second.original_text == "Framing under way"
    assert second.source_row == "3"


@pytest.mark.integration
def test_observations_are_returned_but_not_persisted(db_session, project):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "text": "Framing under way",
                    "reported_activity_id": "A-102",
                    "reported_status": "in progress",
                    "reported_progress": "40",
                    "reported_date": "2026-11-05",
                }
            ]
        ),
    )

    assert len(result.reports) == 1
    imported = result.reports[0]

    assert imported.row_number == 2
    assert imported.reported_activity_id == "A-102"
    assert imported.reported_status == "in_progress"
    assert imported.reported_progress == 40.0
    assert imported.reported_date == date(2026, 11, 5)

    # The report row itself carries only text and provenance; the
    # observations await the extraction step.
    stored = stored_reports(db_session, project.id)[0]
    assert stored.id == imported.report_id
    assert not hasattr(stored, "reported_status")


@pytest.mark.integration
def test_import_does_not_touch_planned_activities(db_session, project):
    import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [
                {
                    "text": "Foundation complete",
                    "reported_activity_id": "A-101",
                    "reported_progress": "100",
                }
            ]
        ),
    )

    db_session.expire_all()
    assert db_session.query(PlannedActivity).count() == 0


@pytest.mark.integration
def test_invalid_rows_are_reported_and_valid_rows_still_import(
    db_session, project
):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [
                {"text": "Valid statement"},
                {"text": "", "reported_progress": "40"},
                {"text": "Bad status", "reported_status": "nearly done"},
                {},
            ]
        ),
    )

    assert result.inserted_count == 1
    assert result.invalid_count == 2
    assert result.skipped_empty_count == 1

    reports = stored_reports(db_session, project.id)
    assert [report.original_text for report in reports] == [
        "Valid statement"
    ]


@pytest.mark.integration
def test_repeated_text_is_imported_and_counted_as_duplicate(
    db_session, project
):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [
                {"text": "Framing under way"},
                {"text": "framing under way"},
            ]
        ),
    )

    assert result.inserted_count == 2
    assert result.duplicate_count == 2
    assert result.invalid_count == 0
    assert len(stored_reports(db_session, project.id)) == 2


@pytest.mark.integration
def test_reports_are_scoped_to_the_target_project(
    db_session, project, project_b
):
    parsed_file = make_parsed_file([{"text": "Site cleared"}])

    import_progress_reports(db_session, project.id, parsed_file)
    import_progress_reports(db_session, project_b.id, parsed_file)

    for owner in (project, project_b):
        reports = stored_reports(db_session, owner.id)
        assert len(reports) == 1
        assert reports[0].project_id == owner.id


@pytest.mark.integration
def test_nothing_is_inserted_when_every_row_is_invalid(db_session, project):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file([{"text": ""}, {"text": "   "}]),
    )

    assert result.inserted_count == 0
    assert result.invalid_count == 2
    assert stored_reports(db_session, project.id) == []


@pytest.mark.integration
def test_unknown_project_rolls_back_and_raises(db_session):
    with pytest.raises(Exception):
        import_progress_reports(
            db_session,
            uuid4(),
            make_parsed_file([{"text": "Orphan report"}]),
        )

    # The failed import left no partial state behind.
    db_session.expire_all()
    assert db_session.query(ProgressReport).count() == 0


@pytest.mark.integration
def test_unmapped_fields_are_surfaced_on_the_result(db_session, project):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [{"text": "Site cleared"}],
            unmapped_fields=["reported_status", "reported_progress"],
        ),
    )

    assert result.unmapped_fields == [
        "reported_status",
        "reported_progress",
    ]


def test_import_rolls_back_and_reraises_database_errors():
    session = MagicMock()
    session.commit.side_effect = RuntimeError("Database write failed")

    with pytest.raises(RuntimeError, match="Database write failed"):
        import_progress_reports(
            session,
            uuid4(),
            make_parsed_file([{"text": "Site cleared"}]),
        )

    session.rollback.assert_called_once()
