"""
Shared parsing and normalization for imported cell values.

Both importers apply the same rules to dates, percentages and blank cells,
so those rules live here rather than being restated per importer.
"""

from datetime import date, datetime
from math import isfinite
from typing import Any

from app.models import ACTIVITY_STATUSES


# Input aliases mapped to the contract's status vocabulary. Keys are compared
# after normalization: lowercased, with separators collapsed to one space.
STATUS_ALIASES: dict[str, str] = {
    "not started": "not_started",
    "notstarted": "not_started",
    "pending": "not_started",
    "to do": "not_started",
    "todo": "not_started",
    "yet to start": "not_started",
    "in progress": "in_progress",
    "inprogress": "in_progress",
    "ongoing": "in_progress",
    "started": "in_progress",
    "wip": "in_progress",
    "work in progress": "in_progress",
    "under way": "in_progress",
    "complete": "completed",
    "completed": "completed",
    "done": "completed",
    "finished": "completed",
    "closed": "completed",
    "blocked": "blocked",
    "blocker": "blocked",
    "stuck": "blocked",
    "impediment": "blocked",
    "on hold": "on_hold",
    "onhold": "on_hold",
    "paused": "on_hold",
    "suspended": "on_hold",
    "deferred": "on_hold",
    "unknown": "unknown",
    "n a": "unknown",
    "na": "unknown",
    "tbd": "unknown",
}


def is_empty(value: Any) -> bool:
    """Treat None and whitespace-only strings as an absent value."""
    return value is None or (
        isinstance(value, str) and not value.strip()
    )


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if not isinstance(value, str):
        raise ValueError("Expected a date in YYYY-MM-DD format.")

    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            "Expected a valid date in YYYY-MM-DD format."
        ) from exc


def parse_progress(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Progress must be a number from 0 to 100.")

    try:
        progress = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Progress must be a number from 0 to 100."
        ) from exc

    if not isfinite(progress):
        raise ValueError("Progress must be a finite number from 0 to 100.")

    if not 0 <= progress <= 100:
        raise ValueError("Progress must be between 0 and 100.")

    return progress


def normalize_status(value: Any) -> str:
    """
    Map a reported status onto the contract's status vocabulary.

    An unrecognized status is an error rather than a silent "unknown", so
    that conflicting or unexpected evidence is reported to a reviewer.
    """
    # A spreadsheet cell may hold a number or a date. Coerce it so an
    # unrecognized value reports the permitted vocabulary either way.
    text = value if isinstance(value, str) else str(value)

    collapsed = " ".join(
        text.strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .split()
    )

    if collapsed in STATUS_ALIASES:
        return STATUS_ALIASES[collapsed]

    raise ValueError(
        "Unrecognized status. Expected one of: "
        + ", ".join(ACTIVITY_STATUSES)
        + "."
    )
