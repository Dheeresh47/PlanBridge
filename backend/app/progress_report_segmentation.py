"""
Splitting a progress report's text into candidate statements.

Segmentation is deterministic and derives nothing: it only decides where one
statement ends and the next begins. The same text always yields the same
segments in the same order, so extraction is reproducible.

A leading list marker is formatting rather than evidence, so it is removed
from the statement. Nothing else is altered, and every segment records the
character range it came from, so the exact source text stays recoverable.
"""

import re
from dataclasses import dataclass


SEGMENTATION_RULE = "lines_and_bullets"

#: A bullet or an ordered-list marker at the start of a line. Kept narrow so
#: a sentence opening with a number or a dash is not mistaken for a marker.
_LIST_MARKER = re.compile(
    r"""
    ^[ \t]*
    (?:
        [-*•·–—]        # - * bullet middot en-dash em-dash
      | \(\s*\d{1,3}\s*\)                  # (1)
      | \d{1,3}\s*[.)]                     # 1. or 1)
      | \(\s*[a-zA-Z]\s*\)                 # (a)
      | [a-zA-Z]\s*[.)](?=[ \t]|$)         # a. or a)
    )
    # Whitespace, or the end of the line for a marker with nothing after it.
    (?:[ \t]+|$)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ReportSegment:
    """One candidate statement and where it came from."""

    index: int
    #: The statement as it appears in the source, less any list marker.
    text: str
    #: Character range of `text` within the report's original text.
    source_start: int
    source_end: int
    #: The list marker removed from the front of the line, if any.
    list_marker: str | None = None


def segment_report_text(original_text: str) -> tuple[list[ReportSegment], int]:
    """
    Split report text into statements, in source order.

    Returns the segments and the number of lines dropped for holding no
    content once their list marker and surrounding whitespace are removed.
    """
    segments: list[ReportSegment] = []
    skipped_blank = 0
    position = 0
    index = 0

    # splitlines(keepends=True) handles \n, \r\n and \r, and keeps offsets
    # aligned with the original text.
    for raw_line in original_text.splitlines(keepends=True):
        line_start = position
        position += len(raw_line)

        line = raw_line.rstrip("\r\n")

        marker_match = _LIST_MARKER.match(line)
        marker = marker_match.group(0) if marker_match else None
        body = line[len(marker) :] if marker else line

        stripped = body.strip()

        if not stripped:
            skipped_blank += 1
            continue

        # Offsets of the stripped statement inside the original text.
        offset_in_body = body.index(stripped)
        start = line_start + (len(marker) if marker else 0) + offset_in_body

        segments.append(
            ReportSegment(
                index=index,
                text=stripped,
                source_start=start,
                source_end=start + len(stripped),
                list_marker=marker.strip() or None if marker else None,
            )
        )
        index += 1

    return segments, skipped_blank
