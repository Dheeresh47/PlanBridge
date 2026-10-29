from datetime import date

from app.progress_report_validation import (
    validate_progress_report_rows,
)


def test_valid_row_is_accepted_with_all_observations():
    result = validate_progress_report_rows(
        [
            {
                "text": "Poured the foundation",
                "reported_activity_id": " A-101 ",
                "reported_status": "Completed",
                "reported_progress": "100",
                "reported_date": "2026-11-02",
            }
        ]
    )

    assert result.errors == []
    assert len(result.valid_rows) == 1

    row = result.valid_rows[0]
    assert row.row_number == 2
    assert row.text == "Poured the foundation"
    assert row.reported_activity_id == "A-101"
    assert row.reported_status == "completed"
    assert row.reported_progress == 100.0
    assert row.reported_date == date(2026, 11, 2)


def test_original_text_is_preserved_without_trimming():
    result = validate_progress_report_rows(
        [{"text": "  Excavation finished.\n  Next: framing.  "}]
    )

    assert result.valid_rows[0].text == (
        "  Excavation finished.\n  Next: framing.  "
    )


def test_missing_text_is_rejected():
    result = validate_progress_report_rows(
        [{"text": "   ", "reported_activity_id": "A-101"}]
    )

    assert result.valid_rows == []
    assert [(error.field, error.message) for error in result.errors] == [
        ("text", "This field is required.")
    ]


def test_absent_optional_values_stay_unknown():
    result = validate_progress_report_rows([{"text": "Site cleared"}])

    row = result.valid_rows[0]
    assert row.reported_activity_id is None
    assert row.reported_status is None
    assert row.reported_progress is None
    assert row.reported_date is None


def test_status_aliases_are_normalized():
    result = validate_progress_report_rows(
        [
            {"text": "a", "reported_status": "IN-PROGRESS"},
            {"text": "b", "reported_status": "on hold"},
            {"text": "c", "reported_status": "Done"},
            {"text": "d", "reported_status": "not_started"},
            {"text": "e", "reported_status": "WIP"},
        ]
    )

    assert result.errors == []
    assert [row.reported_status for row in result.valid_rows] == [
        "in_progress",
        "on_hold",
        "completed",
        "not_started",
        "in_progress",
    ]


def test_unrecognized_status_is_reported_not_silently_unknown():
    result = validate_progress_report_rows(
        [{"text": "a", "reported_status": "nearly there"}]
    )

    assert result.valid_rows == []
    assert result.errors[0].field == "reported_status"
    assert "Unrecognized status" in result.errors[0].message


def test_out_of_range_progress_is_rejected():
    result = validate_progress_report_rows(
        [
            {"text": "a", "reported_progress": "150"},
            {"text": "b", "reported_progress": "-1"},
            {"text": "c", "reported_progress": "halfway"},
        ]
    )

    assert result.valid_rows == []
    assert {error.row_number for error in result.errors} == {2, 3, 4}
    assert all(
        error.field == "reported_progress" for error in result.errors
    )


def test_zero_progress_is_kept_distinct_from_absent():
    result = validate_progress_report_rows(
        [
            {"text": "a", "reported_progress": "0"},
            {"text": "b"},
        ]
    )

    assert result.valid_rows[0].reported_progress == 0.0
    assert result.valid_rows[1].reported_progress is None


def test_invalid_date_is_rejected():
    result = validate_progress_report_rows(
        [{"text": "a", "reported_date": "02/11/2026"}]
    )

    assert result.valid_rows == []
    assert result.errors[0].field == "reported_date"


def test_only_wholly_blank_rows_are_skipped():
    result = validate_progress_report_rows(
        [
            {"text": "First"},
            {},
            {"text": "Fourth"},
        ]
    )

    assert result.skipped_empty_rows == 1
    assert [row.row_number for row in result.valid_rows] == [2, 4]


def test_row_with_no_text_is_an_error_not_a_skipped_row():
    """A row may hold content only in a column the mapping ignores."""
    result = validate_progress_report_rows(
        [{"text": None, "reported_status": None}]
    )

    assert result.skipped_empty_rows == 0
    assert result.valid_rows == []
    assert [(error.row_number, error.field) for error in result.errors] == [
        (2, "text")
    ]


def test_repeated_text_is_flagged_but_still_imported():
    result = validate_progress_report_rows(
        [
            {"text": "Framing under way"},
            {"text": "Roof installed"},
            {"text": "  framing   UNDER WAY  "},
        ]
    )

    # Both occurrences remain available as evidence.
    assert len(result.valid_rows) == 3
    assert result.duplicate_text_rows == [2, 4]

    duplicate_errors = [
        error
        for error in result.errors
        if "Report text is repeated" in error.message
    ]
    assert {error.row_number for error in duplicate_errors} == {2, 4}
    assert "rows 2, 4" in duplicate_errors[0].message


def test_errors_are_sorted_by_row_then_field():
    result = validate_progress_report_rows(
        [
            {"text": "b", "reported_date": "nope"},
            {"text": "", "reported_progress": "500"},
        ]
    )

    assert [
        (error.row_number, error.field) for error in result.errors
    ] == [
        (2, "reported_date"),
        (3, "reported_progress"),
        (3, "text"),
    ]
