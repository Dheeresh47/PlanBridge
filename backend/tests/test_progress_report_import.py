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
from app.progress_report_validation import (
    EXISTING_REPORT_SCOPE,
    UPLOAD_SCOPE,
)


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


@pytest.mark.integration
def test_reupload_flags_a_potential_duplicate_of_an_existing_report(
    db_session, project
):
    first = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file([{"text": "Framing under way"}]),
    )
    assert first.duplicate_count == 0

    second = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [{"text": "  framing   UNDER WAY  "}],
            source_filename="week-2.csv",
        ),
    )

    # Still imported: repeated text is never discarded.
    assert second.inserted_count == 1
    assert second.invalid_count == 0
    assert second.duplicate_count == 1
    assert len(stored_reports(db_session, project.id)) == 2

    flag = second.duplicate_flags[0]
    assert flag.scope == EXISTING_REPORT_SCOPE
    assert flag.row_number == 2
    assert flag.imported is True

    # Reported as a possibility, not as proof.
    assert "potential duplicate" in flag.message
    assert "matches an existing report" in flag.message

    # The existing record is identified.
    existing = stored_reports(db_session, project.id)[0]
    assert flag.existing_report_id == existing.id
    assert flag.existing_source_filename == "week-1.csv"
    assert flag.existing_source_row == "2"
    assert "'week-1.csv' row 2" in flag.message


@pytest.mark.integration
def test_existing_report_matching_is_scoped_to_the_target_project(
    db_session, project, project_b
):
    parsed_file = make_parsed_file([{"text": "Site cleared"}])

    import_progress_reports(db_session, project.id, parsed_file)

    # Identical text in a different project is not a duplicate.
    other = import_progress_reports(db_session, project_b.id, parsed_file)

    assert other.inserted_count == 1
    assert other.duplicate_count == 0
    assert other.duplicate_flags == []

    # And re-uploading into the original project still flags.
    again = import_progress_reports(db_session, project.id, parsed_file)
    assert again.duplicate_count == 1
    assert again.duplicate_flags[0].scope == EXISTING_REPORT_SCOPE


@pytest.mark.integration
def test_rows_in_this_upload_are_not_matched_against_themselves(
    db_session, project
):
    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file([{"text": "Only statement"}]),
    )

    assert result.inserted_count == 1
    assert result.duplicate_count == 0


@pytest.mark.integration
def test_invalid_row_matching_an_existing_report_is_flagged_not_imported(
    db_session, project
):
    import_progress_reports(
        db_session,
        project.id,
        make_parsed_file([{"text": "Framing under way"}]),
    )

    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [{"text": "Framing under way", "reported_progress": "999"}],
            source_filename="week-2.csv",
        ),
    )

    assert result.inserted_count == 0
    assert result.invalid_count == 1
    assert result.duplicate_count == 1

    flag = result.duplicate_flags[0]
    assert flag.scope == EXISTING_REPORT_SCOPE
    # Flagged as repeated evidence, but rejected for an unrelated reason.
    assert flag.imported is False

    # Only the first upload's report is stored.
    assert len(stored_reports(db_session, project.id)) == 1


@pytest.mark.integration
def test_duplicate_flags_do_not_disturb_the_count_partition(
    db_session, project
):
    rows = [
        {"text": "Repeated statement"},
        {"text": "repeated statement"},
        {"text": "Bad status", "reported_status": "nearly done"},
        {"text": ""},
        {},
    ]

    result = import_progress_reports(
        db_session, project.id, make_parsed_file(rows)
    )

    # The three blocking counts partition the source rows exactly.
    assert (
        result.inserted_count
        + result.invalid_count
        + result.skipped_empty_count
    ) == len(rows)
    assert result.inserted_count == 2
    assert result.invalid_count == 2
    assert result.skipped_empty_count == 1

    # Duplicates are a diagnostic over the same rows, counted separately.
    assert result.duplicate_count == 2
    assert result.duplicate_rows == [2, 3]
    assert all(flag.imported for flag in result.duplicate_flags)


@pytest.mark.integration
def test_same_upload_and_existing_matches_are_both_reported(
    db_session, project
):
    import_progress_reports(
        db_session,
        project.id,
        make_parsed_file([{"text": "Shared statement"}]),
    )

    result = import_progress_reports(
        db_session,
        project.id,
        make_parsed_file(
            [{"text": "Shared statement"}, {"text": "shared statement"}],
            source_filename="week-2.csv",
        ),
    )

    assert result.inserted_count == 2
    assert result.duplicate_rows == [2, 3]

    scopes = {
        (flag.row_number, flag.scope) for flag in result.duplicate_flags
    }
    assert scopes == {
        (2, UPLOAD_SCOPE),
        (2, EXISTING_REPORT_SCOPE),
        (3, UPLOAD_SCOPE),
        (3, EXISTING_REPORT_SCOPE),
    }

    # One row, counted once, despite carrying two flags.
    assert result.duplicate_count == 2
