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
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.models import PlannedActivity, ProgressEvent, ProgressReport
from app.progress_event_extraction import (
    APPLIED,
    NOT_SUPPLIED,
    OBSERVATIONS_REASON_KEY,
    OBSERVATIONS_STATE_KEY,
    REPORT_OBSERVATIONS_KEY,
    WITHHELD,
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

    # The key is always present, so a consumer reads one place.
    assert metadata[REPORT_OBSERVATIONS_KEY] is None
    assert metadata[OBSERVATIONS_STATE_KEY] == NOT_SUPPLIED
    assert metadata[OBSERVATIONS_REASON_KEY] is None


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
    assert metadata[OBSERVATIONS_STATE_KEY] == APPLIED
    assert metadata[OBSERVATIONS_REASON_KEY] is None

    supplied = metadata[REPORT_OBSERVATIONS_KEY]
    assert supplied["reported_status"] == "completed"
    assert supplied["reported_date"] == "2026-11-09"
    assert supplied["reported_activity_id"] == "A-110"
    assert supplied["reported_progress"] == 100.0


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
        assert metadata[OBSERVATIONS_STATE_KEY] == WITHHELD

        # Not lost: durably recorded under the same key as when applied,
        # with an explicit reason beside it.
        supplied = metadata[REPORT_OBSERVATIONS_KEY]
        assert supplied["reported_activity_id"] == "A-110"
        assert supplied["reported_progress"] == 100.0
        assert supplied["reported_date"] == "2026-11-09"
        assert "would assert something the report does not say" in (
            metadata[OBSERVATIONS_REASON_KEY]
        )


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
    assert event.extraction_metadata[REPORT_OBSERVATIONS_KEY] is None
    assert (
        event.extraction_metadata[OBSERVATIONS_STATE_KEY] == NOT_SUPPLIED
    )


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


@pytest.mark.integration
def test_metadata_uses_one_key_and_shape_whether_applied_or_withheld(
    db_session, project
):
    """A reviewer reads the same key either way, and the state tells which."""
    single = import_one(
        db_session,
        project,
        "Roof installed",
        reported_activity_id="A-110",
        reported_progress="100",
    )
    multiple = import_one(
        db_session,
        project,
        "Roof installed\nGlazing started",
        source_filename="week-2.csv",
        reported_activity_id="A-110",
        reported_progress="100",
    )

    extract_progress_events(
        db_session, single.report_id, observations_from_import(single)
    )
    extract_progress_events(
        db_session, multiple.report_id, observations_from_import(multiple)
    )

    applied = stored_events(db_session, single.report_id)[0]
    withheld = stored_events(db_session, multiple.report_id)[0]

    # Same key, same shape, regardless of treatment.
    assert (
        applied.extraction_metadata[REPORT_OBSERVATIONS_KEY]
        == withheld.extraction_metadata[REPORT_OBSERVATIONS_KEY]
    )
    assert set(
        applied.extraction_metadata[REPORT_OBSERVATIONS_KEY]
    ) == {
        "reported_activity_id",
        "reported_status",
        "reported_progress",
        "reported_date",
    }

    # The state and reason are what differ.
    assert applied.extraction_metadata[OBSERVATIONS_STATE_KEY] == APPLIED
    assert withheld.extraction_metadata[OBSERVATIONS_STATE_KEY] == WITHHELD
    assert applied.extraction_metadata[OBSERVATIONS_REASON_KEY] is None
    assert withheld.extraction_metadata[OBSERVATIONS_REASON_KEY]

    # And the event-level values: applied on one, unknown on the other.
    assert applied.reported_activity_id == "A-110"
    assert withheld.reported_activity_id is None


@pytest.mark.integration
def test_withheld_values_are_distinguishable_from_never_supplied(
    db_session, project
):
    """The distinction downstream review needs, recorded on the event."""
    silent = import_one(db_session, project, "Site cleared\nFence erected")
    withholding = import_one(
        db_session,
        project,
        "Roof installed\nGlazing started",
        source_filename="week-2.csv",
        reported_progress="100",
    )

    extract_progress_events(db_session, silent.report_id)
    extract_progress_events(
        db_session,
        withholding.report_id,
        observations_from_import(withholding),
    )

    silent_event = stored_events(db_session, silent.report_id)[0]
    withheld_event = stored_events(db_session, withholding.report_id)[0]

    # Both have an unknown event-level progress.
    assert silent_event.reported_progress is None
    assert withheld_event.reported_progress is None

    # But only one had a known report-level value that could not be assigned.
    assert (
        silent_event.extraction_metadata[OBSERVATIONS_STATE_KEY]
        == NOT_SUPPLIED
    )
    assert (
        withheld_event.extraction_metadata[OBSERVATIONS_STATE_KEY]
        == WITHHELD
    )
    assert (
        withheld_event.extraction_metadata[REPORT_OBSERVATIONS_KEY][
            "reported_progress"
        ]
        == 100.0
    )


def test_observations_from_import_accepts_an_imported_progress_report():
    from app.progress_report_import import ImportedProgressReport

    imported = ImportedProgressReport(
        report_id=uuid4(),
        row_number=2,
        reported_activity_id="A-110",
        reported_status="completed",
        reported_progress=100.0,
        reported_date=date(2026, 11, 9),
    )

    observations = observations_from_import(imported)

    assert observations.reported_activity_id == "A-110"
    assert observations.reported_status == "completed"
    assert observations.reported_progress == 100.0
    assert observations.reported_date == date(2026, 11, 9)
    assert observations.any_supplied is True


@pytest.mark.integration
def test_concurrent_extraction_of_one_report_is_serialized(
    concurrent_extraction,
):
    """
    The existing-event check must hold against a competing extraction.

    Without the report row lock both attempts find no events under
    read-committed isolation and both insert. Here the first attempt holds a
    lock on the report, so the second cannot reach its check: it waits, and
    the short lock_timeout turns that wait into a visible failure rather
    than a test that hangs.

    The holder takes FOR NO KEY UPDATE rather than FOR UPDATE deliberately.
    FOR UPDATE would also block the child insert through the foreign key's
    own FOR KEY SHARE check, so the test would pass whether or not
    extraction locks anything. FOR NO KEY UPDATE permits that insert and
    conflicts only with extraction's own FOR UPDATE, so this fails if the
    lock is removed.
    """
    setup = concurrent_extraction

    locked = setup.first.execute(
        text(
            "SELECT id FROM progress_reports WHERE id = :rid "
            "FOR NO KEY UPDATE"
        ),
        {"rid": setup.report_id},
    ).scalar()
    assert locked is not None

    setup.second.execute(text("SET LOCAL lock_timeout = '400ms'"))

    with pytest.raises(OperationalError):
        extract_progress_events(setup.second, setup.report_id)

    # The blocked attempt inserted nothing.
    setup.second.rollback()
    assert (
        setup.second.query(ProgressEvent)
        .filter(ProgressEvent.report_id == setup.report_id)
        .count()
        == 0
    )


@pytest.mark.integration
def test_extraction_of_another_report_is_not_blocked(
    concurrent_extraction, db_session, project
):
    """Locking is per report, so unrelated extractions still proceed."""
    setup = concurrent_extraction

    setup.first.execute(
        text(
            "SELECT id FROM progress_reports WHERE id = :rid FOR UPDATE"
        ),
        {"rid": setup.report_id},
    )

    # A different report, on a different connection, extracts normally.
    other = import_one(db_session, project, "Glazing started")
    result = extract_progress_events(db_session, other.report_id)

    assert result.created_count == 1
    assert result.conflict_count == 0


@pytest.mark.integration
def test_the_second_attempt_refuses_once_the_first_commits(
    concurrent_extraction,
):
    """
    The refusal policy is the same sequential or concurrent.

    Once the first extraction commits and releases the lock, the second
    acquires it, sees the committed events and takes the ordinary conflict
    path rather than inserting again.
    """
    setup = concurrent_extraction

    first = extract_progress_events(setup.first, setup.report_id)
    assert first.created_count == 1

    second = extract_progress_events(setup.second, setup.report_id)

    assert second.created_count == 0
    assert second.conflict_count == 1
    assert second.events == []
    assert any(
        "already has 1 extracted event" in issue.message
        for issue in second.issues
    )

    # Exactly one event exists, from the first attempt.
    events = (
        setup.second.query(ProgressEvent)
        .filter(ProgressEvent.report_id == setup.report_id)
        .all()
    )
    assert len(events) == 1
    assert events[0].id == first.events[0].event_id


@pytest.mark.integration
def test_extraction_takes_a_row_lock_on_the_report(concurrent_extraction):
    """The lock is what closes the window, so assert it is really taken."""
    setup = concurrent_extraction

    statements: list[str] = []

    @event.listens_for(setup.second.get_bind(), "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        extract_progress_events(setup.second, setup.report_id)
    finally:
        event.remove(
            setup.second.get_bind(), "before_cursor_execute", record
        )

    locking = [
        statement
        for statement in statements
        if "FOR UPDATE" in statement.upper()
        and "progress_reports" in statement
    ]
    assert len(locking) == 1
