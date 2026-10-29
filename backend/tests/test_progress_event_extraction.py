"""
Database-backed tests for progress-event extraction.

These run against planbridge_test. The mocked test at the end covers only
the rollback path.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models import PlannedActivity, ProgressEvent
from app.progress_event_extraction import (
    ProgressEventExtractionError,
    ReportObservations,
    extract_progress_events,
    observations_from_import,
)
from app.progress_report_import import import_progress_reports
from app.progress_report_reader import ParsedReportFile


def make_parsed_file(rows, source_filename="week-1.csv"):
    return ParsedReportFile(
        source_filename=source_filename,
        source_format="csv",
        headers=["text", "activity_id", "status", "progress", "date"],
        mapped_columns={"text": "text"},
        unmapped_fields=[],
        rows=rows,
    )


def import_one(session, project, text, **observations):
    """Import a single report row and return its ImportedProgressReport."""
    row = {"text": text}
    row.update(observations)

    result = import_progress_reports(
        session, project.id, make_parsed_file([row])
    )
    assert result.inserted_count == 1
    return result.reports[0]


def stored_events(session, report_id):
    session.expire_all()
    return (
        session.query(ProgressEvent)
        .filter(ProgressEvent.report_id == report_id)
        .order_by(ProgressEvent.event_text)
        .all()
    )


@pytest.mark.integration
def test_single_statement_report_yields_one_event(db_session, project):
    imported = import_one(db_session, project, "Poured the foundation.")

    result = extract_progress_events(db_session, imported.report_id)

    assert result.created_count == 1
    assert result.conflict_count == 0
    assert result.skipped_blank_segment_count == 0
    assert result.observations_withheld is False
    assert result.issues == []

    events = stored_events(db_session, imported.report_id)
    assert len(events) == 1

    event = events[0]
    assert event.report_id == imported.report_id
    assert event.event_text == "Poured the foundation."
    assert event.normalized_text == "poured the foundation."

    # Nothing was derived from the prose.
    assert event.reported_activity_id is None
    assert event.reported_status is None
    assert event.reported_progress is None
    assert event.reported_date is None

    metadata = event.extraction_metadata
    assert metadata["segmentation"] == "lines_and_bullets"
    assert metadata["segment_index"] == 0
    assert metadata["segment_count"] == 1
    assert metadata["observations_applied"] is False


@pytest.mark.integration
def test_multi_line_report_yields_an_event_per_statement(
    db_session, project
):
    imported = import_one(
        db_session,
        project,
        "- Poured the foundation\n"
        "\n"
        "- Framing under way\n"
        "- Roof pending\n",
    )

    result = extract_progress_events(db_session, imported.report_id)

    assert result.created_count == 3
    assert result.skipped_blank_segment_count == 1

    assert [event.event_text for event in result.events] == [
        "Poured the foundation",
        "Framing under way",
        "Roof pending",
    ]
    assert [event.segment_index for event in result.events] == [0, 1, 2]
    assert len(stored_events(db_session, imported.report_id)) == 3


@pytest.mark.integration
def test_observations_attach_only_to_a_single_statement_report(
    db_session, project
):
    imported = import_one(
        db_session,
        project,
        "Roof installed",
        reported_activity_id="A-110",
        reported_status="Completed",
        reported_progress="100",
        reported_date="2026-11-09",
    )

    result = extract_progress_events(
        db_session,
        imported.report_id,
        observations_from_import(imported),
    )

    assert result.created_count == 1
    assert result.observations_withheld is False

    event = stored_events(db_session, imported.report_id)[0]
    assert event.reported_activity_id == "A-110"
    assert event.reported_status == "completed"
    assert event.reported_progress == Decimal("100.00")
    assert event.reported_date == date(2026, 11, 9)

    metadata = event.extraction_metadata
    assert metadata["observations_applied"] is True
    assert metadata["report_observations"]["reported_status"] == "completed"
    assert metadata["report_observations"]["reported_date"] == "2026-11-09"


@pytest.mark.integration
def test_observations_are_withheld_from_a_multi_statement_report(
    db_session, project
):
    """Attaching one report's progress to one of several statements would
    assert something the report does not say."""
    imported = import_one(
        db_session,
        project,
        "Roof installed\nGlazing started",
        reported_activity_id="A-110",
        reported_progress="100",
        reported_date="2026-11-09",
    )

    result = extract_progress_events(
        db_session,
        imported.report_id,
        observations_from_import(imported),
    )

    assert result.created_count == 2
    assert result.observations_withheld is True
    assert any(issue.scope == "observations" for issue in result.issues)

    for event in stored_events(db_session, imported.report_id):
        assert event.reported_activity_id is None
        assert event.reported_progress is None
        assert event.reported_date is None

        metadata = event.extraction_metadata
        assert metadata["observations_applied"] is False

        # Not lost: recorded as withheld for a reviewer.
        withheld = metadata["withheld_report_observations"]
        assert withheld["reported_activity_id"] == "A-110"
        assert withheld["reported_progress"] == 100.0
        assert withheld["reported_date"] == "2026-11-09"


@pytest.mark.integration
def test_no_observations_supplied_leaves_fields_unknown(
    db_session, project
):
    imported = import_one(db_session, project, "Site cleared")

    extract_progress_events(
        db_session, imported.report_id, ReportObservations()
    )

    event = stored_events(db_session, imported.report_id)[0]
    assert event.reported_status is None
    assert "report_observations" not in event.extraction_metadata
    assert "withheld_report_observations" not in event.extraction_metadata


@pytest.mark.integration
def test_source_offsets_recover_the_statement_from_the_report(
    db_session, project
):
    original = "- Poured the foundation\n\n  Framing under way  \n"
    imported = import_one(db_session, project, original)

    extract_progress_events(db_session, imported.report_id)

    db_session.expire_all()
    events = (
        db_session.query(ProgressEvent)
        .filter(ProgressEvent.report_id == imported.report_id)
        .all()
    )

    for event in events:
        metadata = event.extraction_metadata
        start = metadata["source_start"]
        end = metadata["source_end"]
        assert original[start:end] == event.event_text


@pytest.mark.integration
def test_re_extraction_is_refused_and_leaves_events_unchanged(
    db_session, project
):
    imported = import_one(db_session, project, "Roof installed")

    first = extract_progress_events(db_session, imported.report_id)
    assert first.created_count == 1
    original_id = first.events[0].event_id

    second = extract_progress_events(db_session, imported.report_id)

    assert second.created_count == 0
    assert second.conflict_count == 1
    assert second.events == []
    assert any(
        "already has 1 extracted event" in issue.message
        for issue in second.issues
    )

    # The original event is still the only one, unchanged.
    events = stored_events(db_session, imported.report_id)
    assert len(events) == 1
    assert events[0].id == original_id


@pytest.mark.integration
def test_extraction_does_not_alter_the_report_text(db_session, project):
    original = "  Roof installed.  \nGlazing started.  "
    imported = import_one(db_session, project, original)

    extract_progress_events(db_session, imported.report_id)

    db_session.expire_all()
    from app.models import ProgressReport

    report = db_session.get(ProgressReport, imported.report_id)
    assert report.original_text == original


@pytest.mark.integration
def test_extraction_does_not_touch_planned_activities(db_session, project):
    imported = import_one(
        db_session,
        project,
        "Foundation complete",
        reported_activity_id="A-101",
        reported_progress="100",
    )

    extract_progress_events(
        db_session, imported.report_id, observations_from_import(imported)
    )

    db_session.expire_all()
    assert db_session.query(PlannedActivity).count() == 0


@pytest.mark.integration
def test_unknown_report_is_an_error(db_session):
    missing = uuid4()

    with pytest.raises(ProgressEventExtractionError, match=str(missing)):
        extract_progress_events(db_session, missing)

    db_session.expire_all()
    assert db_session.query(ProgressEvent).count() == 0


@pytest.mark.integration
def test_report_text_with_no_statements_creates_nothing(
    db_session, project
):
    imported = import_one(db_session, project, "-\n   \n")

    result = extract_progress_events(db_session, imported.report_id)

    assert result.created_count == 0
    assert result.skipped_blank_segment_count == 2
    assert result.conflict_count == 0
    assert any(
        "no statement to extract" in issue.message
        for issue in result.issues
    )
    assert stored_events(db_session, imported.report_id) == []


@pytest.mark.integration
def test_events_are_scoped_to_their_own_report(db_session, project):
    first = import_one(db_session, project, "Shared statement")
    second = import_one(
        db_session, project, "Shared statement", source_filename="week-2.csv"
    )

    extract_progress_events(db_session, first.report_id)
    extract_progress_events(db_session, second.report_id)

    for imported in (first, second):
        events = stored_events(db_session, imported.report_id)
        assert len(events) == 1
        assert events[0].report_id == imported.report_id


def test_observations_any_supplied_distinguishes_zero_from_absent():
    assert ReportObservations().any_supplied is False
    assert ReportObservations(reported_progress=0.0).any_supplied is True
    assert ReportObservations(reported_status="blocked").any_supplied is True


def test_extraction_rolls_back_and_reraises_database_errors():
    session = MagicMock()
    session.get.return_value = MagicMock(original_text="Roof installed")
    session.query.return_value.filter.return_value.count.return_value = 0
    session.commit.side_effect = RuntimeError("Database write failed")

    with pytest.raises(RuntimeError, match="Database write failed"):
        extract_progress_events(session, uuid4())

    session.rollback.assert_called_once()
