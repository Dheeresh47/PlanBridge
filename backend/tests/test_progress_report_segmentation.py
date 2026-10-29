from app.progress_report_segmentation import (
    SEGMENTATION_RULE,
    segment_report_text,
)


def texts(segments):
    return [segment.text for segment in segments]


def test_single_line_report_yields_exactly_one_event():
    segments, skipped = segment_report_text("Poured the foundation.")

    assert texts(segments) == ["Poured the foundation."]
    assert segments[0].index == 0
    assert skipped == 0


def test_lines_are_split_in_source_order():
    segments, skipped = segment_report_text(
        "Poured the foundation.\nFraming under way.\nRoof pending."
    )

    assert texts(segments) == [
        "Poured the foundation.",
        "Framing under way.",
        "Roof pending.",
    ]
    assert [segment.index for segment in segments] == [0, 1, 2]
    assert skipped == 0


def test_list_markers_are_removed_as_formatting():
    segments, _ = segment_report_text(
        "- Poured the foundation\n"
        "* Framing under way\n"
        "• Roof pending\n"
        "1. Glazing ordered\n"
        "2) Cladding delayed\n"
        "(3) Snagging open\n"
    )

    assert texts(segments) == [
        "Poured the foundation",
        "Framing under way",
        "Roof pending",
        "Glazing ordered",
        "Cladding delayed",
        "Snagging open",
    ]
    assert [segment.list_marker for segment in segments] == [
        "-",
        "*",
        "•",
        "1.",
        "2)",
        "(3)",
    ]


def test_blank_and_marker_only_lines_are_skipped_and_counted():
    segments, skipped = segment_report_text(
        "First statement\n"
        "\n"
        "   \n"
        "-   \n"
        "Second statement\n"
    )

    assert texts(segments) == ["First statement", "Second statement"]
    assert skipped == 3
    # Indices stay contiguous across the skipped lines.
    assert [segment.index for segment in segments] == [0, 1]


def test_source_offsets_recover_the_original_substring():
    original = (
        "- Poured the foundation\n"
        "\n"
        "  Framing under way  \n"
        "3. Roof pending\n"
    )

    segments, _ = segment_report_text(original)

    for segment in segments:
        assert (
            original[segment.source_start : segment.source_end]
            == segment.text
        )


def test_carriage_returns_and_crlf_are_handled():
    for newline in ("\n", "\r\n", "\r"):
        segments, skipped = segment_report_text(
            f"First{newline}Second{newline}"
        )

        assert texts(segments) == ["First", "Second"], newline
        assert skipped == 0


def test_segmentation_is_deterministic():
    original = "- A\n\n- B\n  C  \n"

    first, first_skipped = segment_report_text(original)
    second, second_skipped = segment_report_text(original)

    assert first == second
    assert first_skipped == second_skipped


def test_a_sentence_opening_with_a_number_is_not_a_list_marker():
    segments, _ = segment_report_text("2026 budget approved")

    assert texts(segments) == ["2026 budget approved"]
    assert segments[0].list_marker is None


def test_internal_punctuation_does_not_split_a_statement():
    segments, _ = segment_report_text(
        "Poured 12.5 m3 today. Framing starts Mon."
    )

    # Sentence splitting is deliberately not part of this rule.
    assert texts(segments) == ["Poured 12.5 m3 today. Framing starts Mon."]


def test_text_with_no_statements_yields_nothing():
    segments, skipped = segment_report_text("   \n-\n\n")

    assert segments == []
    assert skipped == 3


def test_segmentation_rule_is_named_for_the_metadata():
    assert SEGMENTATION_RULE == "lines_and_bullets"
