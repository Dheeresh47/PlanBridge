
import pandas as pd
import pytest

from app.import_validation import validate_planned_activity_rows
from app.planned_activity_reader import (
    PlannedActivityFileError,
    read_planned_activity_file,
)


def write_csv(path, content):
    path.write_text(content, encoding="utf-8")


def test_reads_valid_csv_and_normalizes_headers(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(
        path,
        " external_id , NAME ,planned_progress\n"
        "A-101,Foundation,25\n"
        "A-102,Framing,50\n",
    )

    parsed = read_planned_activity_file(path)

    assert parsed.source_filename == "activities.csv"
    assert parsed.source_format == "csv"
    assert parsed.headers == [
        "external_id",
        "name",
        "planned_progress",
    ]
    assert len(parsed.rows) == 2
    assert parsed.rows[0]["external_id"] == "A-101"
    assert parsed.rows[1]["name"] == "Framing"


def test_preserves_source_row_numbers_across_blank_rows(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(
        path,
        "external_id,name\n"
        "A-101,Foundation\n"
        "\n"
        "A-103,Roof\n",
    )

    parsed = read_planned_activity_file(path)
    result = validate_planned_activity_rows(parsed.rows)

    assert parsed.rows[1] == {}
    assert [row.row_number for row in result.valid_rows] == [2, 4]
    assert result.skipped_empty_rows == 1


def test_rejects_missing_required_headers(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(path, "external_id,description\nA-101,Foundation\n")

    with pytest.raises(PlannedActivityFileError, match="Missing required"):
        read_planned_activity_file(path)


def test_rejects_duplicate_headers_after_normalization(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(
        path,
        "external_id,name, NAME \n"
        "A-101,Foundation,Other\n",
    )

    with pytest.raises(PlannedActivityFileError, match="Duplicate column"):
        read_planned_activity_file(path)


def test_rejects_empty_header_cells(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(path, "external_id,,planned_progress\nA-101,Foundation,25\n")

    with pytest.raises(PlannedActivityFileError, match="empty column name"):
        read_planned_activity_file(path)


def test_rejects_unsupported_columns(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(
        path,
        "external_id,name,actual_cost\nA-101,Foundation,5000\n",
    )

    with pytest.raises(PlannedActivityFileError, match="Unsupported column"):
        read_planned_activity_file(path)


def test_rejects_unsupported_file_extension(tmp_path):
    path = tmp_path / "activities.xls"
    path.write_text("external_id,name\nA-101,Foundation\n", encoding="utf-8")

    with pytest.raises(PlannedActivityFileError, match="Unsupported file type"):
        read_planned_activity_file(path)


def test_rejects_nonexistent_file(tmp_path):
    path = tmp_path / "missing.csv"

    with pytest.raises(PlannedActivityFileError, match="does not exist"):
        read_planned_activity_file(path)


def test_reads_xlsx_file(tmp_path):
    path = tmp_path / "activities.xlsx"

    pd.DataFrame(
        [
            ["external_id", "name", "planned_progress"],
            ["A-101", "Foundation", 25],
            ["A-102", "Framing", 50],
        ]
    ).to_excel(path, index=False, header=False)

    parsed = read_planned_activity_file(path)

    assert parsed.source_format == "xlsx"
    assert parsed.headers == [
        "external_id",
        "name",
        "planned_progress",
    ]
    assert len(parsed.rows) == 2
    assert parsed.rows[0]["external_id"] == "A-101"
    assert parsed.rows[1]["planned_progress"] == 50


def test_reader_does_not_validate_or_write_database_records(tmp_path):
    path = tmp_path / "activities.csv"
    write_csv(
        path,
        "external_id,name,planned_progress\n"
        "A-101,Foundation,150\n",
    )

    parsed = read_planned_activity_file(path)
    validation = validate_planned_activity_rows(parsed.rows)

    assert parsed.rows[0]["external_id"] == "A-101"
    assert validation.valid_rows == []
    assert any(
        error.field == "planned_progress"
        for error in validation.errors
    )
