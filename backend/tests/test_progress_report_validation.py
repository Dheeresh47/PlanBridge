from datetime import date

from app.progress_report_validation import (
    UPLOAD_SCOPE,
    comparable_text,
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


def test_repeated_text_flags_every_row_against_a_reference_occurrence():
    result = validate_progress_report_rows(
        [
            {"text": "Framing under way"},
            {"text": "Roof installed"},
            {"text": "  framing   UNDER WAY  "},
        ]
    )

    # Both occurrences remain available as evidence.
    assert len(result.valid_rows) == 3

    # Duplicates are diagnostics, not blocking errors.
    assert result.errors == []

    flags = result.duplicate_flags
    assert [flag.row_number for flag in flags] == [2, 4]
    assert {flag.scope for flag in flags} == {UPLOAD_SCOPE}

    # The lowest source row is the deterministic reference occurrence,
    # and the first occurrence is flagged too.
    assert {flag.reference_row for flag in flags} == {2}
    assert "rows 2, 4" in flags[0].message
    assert "first occurrence is row 2" in flags[0].message


def test_duplicate_flags_are_deterministic_across_runs():
    rows = [
        {"text": "Same statement"},
        {"text": "same STATEMENT"},
        {"text": "Same  statement"},
    ]

    first = validate_progress_report_rows(rows)
    second = validate_progress_report_rows(rows)

    signature = lambda result: [
        (flag.row_number, flag.scope, flag.reference_row, flag.message)
        for flag in result.duplicate_flags
    ]

    assert signature(first) == signature(second)
    assert [flag.row_number for flag in first.duplicate_flags] == [2, 3, 4]


def test_duplicate_detection_includes_rows_with_other_errors():
    """A row rejected for another reason still reveals repeated evidence."""
    result = validate_progress_report_rows(
        [
            {"text": "Same text", "reported_progress": "999"},
            {"text": "Same text"},
        ]
    )

    # Row 2 is invalid, row 3 is valid.
    assert [error.row_number for error in result.errors] == [2]
    assert [row.row_number for row in result.valid_rows] == [3]

    # Both are still flagged as repeating each other.
    assert [flag.row_number for flag in result.duplicate_flags] == [2, 3]
    assert {flag.reference_row for flag in result.duplicate_flags} == {2}


def test_blank_text_rows_are_not_grouped_as_duplicates():
    result = validate_progress_report_rows(
        [{"text": ""}, {"text": "   "}]
    )

    assert result.duplicate_flags == []
    assert [error.row_number for error in result.errors] == [2, 3]


def test_comparable_text_is_recorded_for_every_nonblank_row():
    result = validate_progress_report_rows(
        [
            {"text": "  Framing   under way "},
            {"text": "", "reported_progress": "5"},
            {"text": "Bad date", "reported_date": "nope"},
        ]
    )

    # Includes the invalid row 4, excludes the blank-text row 3.
    assert result.comparable_text_by_row == {
        2: "framing under way",
        4: "bad date",
    }


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


def test_non_string_status_reports_the_permitted_vocabulary():
    """A spreadsheet cell may hold a number or a date, not a status word."""
    result = validate_progress_report_rows(
        [
            {"text": "a", "reported_status": 42},
            {"text": "b", "reported_status": 3.5},
        ]
    )

    assert result.valid_rows == []
    assert [error.row_number for error in result.errors] == [2, 3]

    for error in result.errors:
        assert error.field == "reported_status"
        assert "Unrecognized status" in error.message
        assert "not_started" in error.message
        assert "unknown" in error.message


def test_comparable_text_normalizes_case_and_whitespace():
    assert comparable_text("  Framing   UNDER\tway \n") == "framing under way"
    assert comparable_text("already normal") == "already normal"
