"""
Extracting structured progress events from a stored report.

Extraction derives nothing the source does not state. It segments the
report's preserved text, stores each statement as an event, and keeps the
normalized matching text separate from that statement.

Reported identifiers, statuses, percentages and dates come only from the
explicit columns validated at import. Those observations describe the report
rather than any one statement in it, so they are attached only when the
report yields exactly one event; otherwise they are recorded as withheld.

Extraction matches no activities, approves nothing, and never modifies
planned activities or the report's original text.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import ProgressEvent, ProgressReport
from app.progress_report_import import ImportedProgressReport
from app.progress_report_segmentation import (
    SEGMENTATION_RULE,
    segment_report_text,
)
from app.progress_report_validation import comparable_text


#: Metadata key holding the observations the report supplied, in the same
#: shape whether they were applied to the event or withheld from it.
REPORT_OBSERVATIONS_KEY = "report_observations"

#: Metadata key stating what became of those observations.
OBSERVATIONS_STATE_KEY = "report_observations_state"

#: Metadata key explaining a withholding, None otherwise.
OBSERVATIONS_REASON_KEY = "report_observations_withheld_reason"

APPLIED = "applied"
WITHHELD = "withheld"
NOT_SUPPLIED = "not_supplied"

WITHHELD_REASON_MULTIPLE_STATEMENTS = (
    "The report supplied reported values but yielded several statements. "
    "Assigning them to one statement would assert something the report does "
    "not say, so they were not applied to any event."
)


class ProgressEventExtractionError(ValueError):
    """Raised when a report cannot be extracted at all."""


@dataclass(frozen=True)
class ReportObservations:
    """The explicit column values an import supplied for one report."""

    reported_activity_id: str | None = None
    reported_status: str | None = None
    reported_progress: float | None = None
    reported_date: date | None = None

    @property
    def any_supplied(self) -> bool:
        return any(
            value is not None
            for value in (
                self.reported_activity_id,
                self.reported_status,
                self.reported_progress,
                self.reported_date,
            )
        )

    def as_metadata(self) -> dict[str, object]:
        """A JSON-safe record of what the report supplied."""
        return {
            "reported_activity_id": self.reported_activity_id,
            "reported_status": self.reported_status,
            "reported_progress": self.reported_progress,
            "reported_date": (
                self.reported_date.isoformat()
                if self.reported_date is not None
                else None
            ),
        }


def observations_from_import(
    imported: ImportedProgressReport,
) -> ReportObservations:
    """
    Build observations from one row of a progress-report import result.

    Extraction consumes what the importer produced, so depending on that
    module is the right direction and introduces no cycle: the importer does
    not import this module.
    """
    return ReportObservations(
        reported_activity_id=imported.reported_activity_id,
        reported_status=imported.reported_status,
        reported_progress=imported.reported_progress,
        reported_date=imported.reported_date,
    )


@dataclass
class ExtractionIssue:
    """Something a reviewer should know about an extraction."""

    scope: str
    message: str


@dataclass
class ExtractedEvent:
    event_id: UUID
    segment_index: int
    event_text: str


@dataclass
class ProgressEventExtractionResult:
    """Summary of extracting one report."""

    report_id: UUID
    created_count: int = 0
    skipped_blank_segment_count: int = 0
    #: 1 when the report already had events and was left untouched.
    conflict_count: int = 0
    #: True when report-level observations could not be attributed to a
    #: single statement and were therefore not applied to any event.
    observations_withheld: bool = False
    issues: list[ExtractionIssue] = field(default_factory=list)
    events: list[ExtractedEvent] = field(default_factory=list)


def extract_progress_events(
    session: Session,
    report_id: UUID,
    observations: ReportObservations | None = None,
) -> ProgressEventExtractionResult:
    """Create progress events for a stored report, without overwriting."""

    result = ProgressEventExtractionResult(report_id=report_id)
    observations = observations or ReportObservations()

    try:
        report = session.get(ProgressReport, report_id)

        if report is None:
            raise ProgressEventExtractionError(
                f"No progress report exists with id {report_id}."
            )

        # Existing events are left untouched: match results and review
        # decisions may already refer to them.
        existing_count = (
            session.query(ProgressEvent)
            .filter(ProgressEvent.report_id == report_id)
            .count()
        )

        if existing_count:
            result.conflict_count = 1
            result.issues.append(
                ExtractionIssue(
                    scope="report",
                    message=(
                        f"This report already has {existing_count} "
                        "extracted event(s); they were left unchanged and "
                        "nothing was inserted."
                    ),
                )
            )
            session.rollback()
            return result

        segments, skipped_blank = segment_report_text(report.original_text)
        result.skipped_blank_segment_count = skipped_blank

        if not segments:
            result.issues.append(
                ExtractionIssue(
                    scope="report",
                    message=(
                        "The report text contains no statement to extract "
                        "once blank lines and list markers are removed."
                    ),
                )
            )
            session.rollback()
            return result

        # Observations describe the report, not a particular statement, so
        # they are only safe to attach when there is exactly one statement.
        attach_observations = (
            len(segments) == 1 and observations.any_supplied
        )

        if observations.any_supplied and not attach_observations:
            result.observations_withheld = True
            result.issues.append(
                ExtractionIssue(
                    scope="observations",
                    message=(
                        f"{WITHHELD_REASON_MULTIPLE_STATEMENTS} The report "
                        f"yielded {len(segments)} statements. The values are "
                        "recorded in each event's extraction metadata."
                    ),
                )
            )

        if not observations.any_supplied:
            observations_state = NOT_SUPPLIED
            withheld_reason = None
        elif attach_observations:
            observations_state = APPLIED
            withheld_reason = None
        else:
            observations_state = WITHHELD
            withheld_reason = WITHHELD_REASON_MULTIPLE_STATEMENTS

        for segment in segments:
            # The observations are recorded under one key in the same shape
            # however they were treated, so a value left unknown because the
            # report said nothing stays distinguishable from one withheld
            # because it could not be assigned safely.
            metadata: dict[str, object] = {
                "segmentation": SEGMENTATION_RULE,
                "segment_index": segment.index,
                "segment_count": len(segments),
                "source_start": segment.source_start,
                "source_end": segment.source_end,
                "list_marker": segment.list_marker,
                REPORT_OBSERVATIONS_KEY: (
                    observations.as_metadata()
                    if observations.any_supplied
                    else None
                ),
                OBSERVATIONS_STATE_KEY: observations_state,
                OBSERVATIONS_REASON_KEY: withheld_reason,
            }

            event = ProgressEvent(
                report_id=report_id,
                event_text=segment.text,
                normalized_text=comparable_text(segment.text),
                reported_activity_id=(
                    observations.reported_activity_id
                    if attach_observations
                    else None
                ),
                reported_status=(
                    observations.reported_status
                    if attach_observations
                    else None
                ),
                reported_progress=(
                    observations.reported_progress
                    if attach_observations
                    else None
                ),
                reported_date=(
                    observations.reported_date
                    if attach_observations
                    else None
                ),
                extraction_metadata=metadata,
            )
            session.add(event)

            # Flush to obtain the identifier without ending the transaction.
            session.flush()

            result.events.append(
                ExtractedEvent(
                    event_id=event.id,
                    segment_index=segment.index,
                    event_text=segment.text,
                )
            )
            result.created_count += 1

        if result.created_count > 0:
            session.commit()
        else:
            session.rollback()

    except Exception:
        session.rollback()
        raise

    return result
