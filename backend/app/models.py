
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


ACTIVITY_STATUSES = (
    "not_started",
    "in_progress",
    "completed",
    "on_hold",
    "cancelled",
    "unknown",
)

REVIEW_DECISIONS = (
    "approved",
    "rejected",
    "needs_clarification",
)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    activities: Mapped[list["PlannedActivity"]] = relationship(
        back_populates="project"
    )
    reports: Mapped[list["ProgressReport"]] = relationship(
        back_populates="project"
    )


class PlannedActivity(Base):
    __tablename__ = "planned_activities"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "external_id",
            name="uq_activity_project_external_id",
        ),
        CheckConstraint(
            "planned_progress IS NULL OR "
            "(planned_progress >= 0 AND planned_progress <= 100)",
            name="ck_activity_planned_progress_range",
        ),
        CheckConstraint(
            "current_progress IS NULL OR "
            "(current_progress >= 0 AND current_progress <= 100)",
            name="ck_activity_current_progress_range",
        ),
        CheckConstraint(
            "status IN "
            "('not_started', 'in_progress', 'completed', "
            "'on_hold', 'cancelled', 'unknown')",
            name="ck_activity_status",
        ),
        CheckConstraint(
            "planned_start IS NULL OR planned_finish IS NULL "
            "OR planned_finish >= planned_start",
            name="ck_activity_date_order",
        ),
        Index("ix_activity_project", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_finish: Mapped[date | None] = mapped_column(Date)
    planned_progress: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2)
    )
    current_progress: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2)
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown",
        server_default="unknown",
    )
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)

    project: Mapped["Project"] = relationship(
        back_populates="activities"
    )


class ProgressReport(Base):
    __tablename__ = "progress_reports"
    __table_args__ = (
        CheckConstraint(
            "source_format IN ('csv', 'xlsx')",
            name="ck_report_source_format",
        ),
        Index("ix_report_project_imported", "project_id", "imported_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    source_format: Mapped[str] = mapped_column(
        String(10), nullable=False
    )
    source_row: Mapped[str | None] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(
        back_populates="reports"
    )
    events: Mapped[list["ProgressEvent"]] = relationship(
        back_populates="report"
    )


class ProgressEvent(Base):
    __tablename__ = "progress_events"
    __table_args__ = (
        CheckConstraint(
            "reported_progress IS NULL OR "
            "(reported_progress >= 0 AND reported_progress <= 100)",
            name="ck_event_reported_progress_range",
        ),
        Index("ix_event_report", "report_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    report_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("progress_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    reported_activity_id: Mapped[str | None] = mapped_column(String(100))
    reported_status: Mapped[str | None] = mapped_column(String(100))
    reported_progress: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2)
    )
    reported_date: Mapped[date | None] = mapped_column(Date)
    extraction_metadata: Mapped[dict | None] = mapped_column(JSONB)

    report: Mapped["ProgressReport"] = relationship(
        back_populates="events"
    )
    match_results: Mapped[list["MatchResult"]] = relationship(
        back_populates="event"
    )


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "activity_id", "matcher_version",
            name="uq_match_event_activity_version",
        ),
        UniqueConstraint(
            "event_id", "matcher_version", "rank",
            name="uq_match_event_version_rank",
        ),
        CheckConstraint(
            "fuzzy_score >= 0 AND fuzzy_score <= 1",
            name="ck_match_fuzzy_score_range",
        ),
        CheckConstraint(
            "tfidf_score >= 0 AND tfidf_score <= 1",
            name="ck_match_tfidf_score_range",
        ),
        CheckConstraint(
            "combined_score >= 0 AND combined_score <= 1",
            name="ck_match_combined_score_range",
        ),
        CheckConstraint("rank >= 1", name="ck_match_rank_positive"),
        Index("ix_match_event_score", "event_id", "combined_score"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("progress_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    activity_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("planned_activities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fuzzy_score: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False
    )
    tfidf_score: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False
    )
    combined_score: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    matcher_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )

    event: Mapped["ProgressEvent"] = relationship(
        back_populates="match_results"
    )
    activity: Mapped["PlannedActivity"] = relationship()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN "
            "('approved', 'rejected', 'needs_clarification')",
            name="ck_review_decision",
        ),
        Index("ix_review_event_decided", "event_id", "decided_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("progress_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    selected_activity_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("planned_activities.id", ondelete="RESTRICT"),
    )
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(150), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False,
    )

    event: Mapped["ProgressEvent"] = relationship()
    selected_activity: Mapped["PlannedActivity | None"] = relationship()


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        Index("ix_audit_project_created", "project_id", "created_at"),
        Index("ix_audit_event", "event_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("progress_events.id", ondelete="RESTRICT")
    )
    decision_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("review_decisions.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False,
    )
