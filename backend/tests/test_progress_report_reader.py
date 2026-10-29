import pandas as pd
import pytest

from app.progress_report_reader import (
    AUTO,
    ColumnMapping,
    ProgressReportFileError,
    read_progress_report_file,
)


def write_csv(path, content):
    path.write_text(content, encoding="utf-8")


def test_reads_csv_with_the_default_mapping(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(
        path,
        " TEXT ,activity_id,status,progress,date\n"
        "Poured the foundation,A-101,completed,100,2026-11-02\n"
        "Framing under way,A-102,In Progress,40,2026-11-05\n",
    )

    parsed = read_progress_report_file(path)

    assert parsed.source_filename == "reports.csv"
    assert parsed.source_format == "csv"
    assert parsed.headers == [
        "text",
        "activity_id",
        "status",
        "progress",
        "date",
    ]
    assert parsed.unmapped_fields == []
    assert parsed.mapped_columns["text"] == "text"
    assert len(parsed.rows) == 2
    assert parsed.rows[0]["text"] == "Poured the foundation"
    assert parsed.rows[1]["reported_activity_id"] == "A-102"


def test_rows_are_keyed_by_logical_field_not_source_column(tmp_path):
    path = tmp_path / "weekly.csv"
    write_csv(
        path,
        "notes,ref,as_of\n"
        "Excavation finished,A-101,2026-11-03\n",
    )

    parsed = read_progress_report_file(
        path,
        mapping=ColumnMapping(
            text="notes",
            reported_activity_id="ref",
            reported_date="as_of",
            reported_status=None,
            reported_progress=None,
        ),
    )

    assert parsed.mapped_columns == {
        "text": "notes",
        "reported_activity_id": "ref",
        "reported_date": "as_of",
    }
    assert sorted(parsed.unmapped_fields) == [
        "reported_progress",
        "reported_status",
    ]
    assert parsed.rows[0] == {
        "text": "Excavation finished",
        "reported_activity_id": "A-101",
        "reported_date": "2026-11-03",
    }


def test_unmapped_source_columns_are_ignored(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(
        path,
        "text,author,department\n"
        "Site cleared,R. Patel,Civil\n",
    )

    parsed = read_progress_report_file(path)

    assert parsed.headers == ["text", "author", "department"]
    assert parsed.rows[0] == {"text": "Site cleared"}


def test_optional_column_absent_from_file_is_reported_as_unmapped(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text\nSite cleared\n")

    parsed = read_progress_report_file(path)

    assert parsed.mapped_columns == {"text": "text"}
    assert sorted(parsed.unmapped_fields) == [
        "reported_activity_id",
        "reported_date",
        "reported_progress",
        "reported_status",
    ]


def test_preserves_source_row_numbers_across_blank_rows(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(
        path,
        "text\n"
        "First statement\n"
        "\n"
        "Third statement\n",
    )

    parsed = read_progress_report_file(path)

    assert parsed.rows[1] == {}
    assert parsed.rows[2]["text"] == "Third statement"


def test_missing_mapped_text_column_is_rejected(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "notes,activity_id\nSite cleared,A-101\n")

    with pytest.raises(ProgressReportFileError) as error:
        read_progress_report_file(path)

    assert "report text column 'text'" in str(error.value)
    assert "notes" in str(error.value)


def test_empty_text_mapping_is_rejected(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text\nSite cleared\n")

    with pytest.raises(ProgressReportFileError, match="report text column"):
        read_progress_report_file(path, mapping=ColumnMapping(text="  "))


def test_unsupported_file_type_is_rejected(tmp_path):
    path = tmp_path / "reports.txt"
    path.write_text("text\nSite cleared\n", encoding="utf-8")

    with pytest.raises(ProgressReportFileError, match="Unsupported file type"):
        read_progress_report_file(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ProgressReportFileError, match="does not exist"):
        read_progress_report_file(tmp_path / "absent.csv")


def test_duplicate_headers_are_rejected(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,TEXT\na,b\n")

    with pytest.raises(ProgressReportFileError, match="Duplicate column"):
        read_progress_report_file(path)


def test_empty_header_cell_is_rejected(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,,status\na,b,c\n")

    with pytest.raises(ProgressReportFileError, match="empty column name"):
        read_progress_report_file(path)


def test_reads_xlsx_and_selects_the_named_sheet(tmp_path):
    path = tmp_path / "reports.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(
            [["Ignore me", "A-000"]],
            columns=["text", "activity_id"],
        ).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(
            [["Roof installed", "A-110"]],
            columns=["text", "activity_id"],
        ).to_excel(writer, sheet_name="Week 1", index=False)

    parsed = read_progress_report_file(path, sheet_name="Week 1")

    assert parsed.source_format == "xlsx"
    assert parsed.rows[0]["text"] == "Roof installed"
    assert parsed.rows[0]["reported_activity_id"] == "A-110"


def test_missing_sheet_is_rejected(tmp_path):
    path = tmp_path / "reports.xlsx"
    pd.DataFrame([["Roof installed"]], columns=["text"]).to_excel(
        path, sheet_name="Week 1", index=False
    )

    with pytest.raises(ProgressReportFileError, match="Could not read"):
        read_progress_report_file(path, sheet_name="Week 9")


def test_utf8_bom_and_non_ascii_text_are_read(tmp_path):
    path = tmp_path / "reports.csv"
    path.write_text(
        "text\nDrainage work completed — 2 m³ removed\n",
        encoding="utf-8-sig",
    )

    parsed = read_progress_report_file(path)

    assert parsed.headers == ["text"]
    assert parsed.rows[0]["text"] == (
        "Drainage work completed — 2 m³ removed"
    )


def test_default_mapping_uses_default_columns_when_present(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,status\nSite cleared,done\n")

    parsed = read_progress_report_file(path)

    assert parsed.mapped_columns == {
        "text": "text",
        "reported_status": "status",
    }
    assert sorted(parsed.unmapped_fields) == [
        "reported_activity_id",
        "reported_date",
        "reported_progress",
    ]


def test_explicit_mapping_naming_a_missing_column_is_rejected(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,status\nSite cleared,done\n")

    with pytest.raises(ProgressReportFileError) as error:
        read_progress_report_file(
            path, mapping=ColumnMapping(reported_progress="progres_typo")
        )

    message = str(error.value)
    assert "progres_typo" in message
    assert "reported_progress" in message
    assert "text, status" in message


def test_field_mapped_to_nothing_is_unmapped_without_error(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,status\nSite cleared,done\n")

    parsed = read_progress_report_file(
        path, mapping=ColumnMapping(reported_status=None)
    )

    assert "reported_status" not in parsed.mapped_columns
    assert "reported_status" in parsed.unmapped_fields
    assert parsed.rows[0] == {"text": "Site cleared"}


def test_auto_is_the_default_for_optional_fields():
    mapping = ColumnMapping()

    assert mapping.reported_status is AUTO
    assert mapping.reported_progress is AUTO
    assert mapping.text == "text"


def test_explicitly_mapped_column_is_used_over_the_default(tmp_path):
    path = tmp_path / "reports.csv"
    write_csv(path, "text,status,official_status\na,draft,done\n")

    parsed = read_progress_report_file(
        path, mapping=ColumnMapping(reported_status="official_status")
    )

    assert parsed.mapped_columns["reported_status"] == "official_status"
    assert parsed.rows[0]["reported_status"] == "done"
