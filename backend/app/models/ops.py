"""Operational tables: jobs, moderation and entity conflicts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    ConflictKind,
    ConflictStatus,
    JobStatus,
    ModerationReason,
    ModerationStatus,
    SourceType,
    pg_enum,
)


class IngestionJob(UUIDMixin, TimestampMixin, Base):
    """One ingestion run.

    `job_key` is UNIQUE and encodes (source, city, time-bucket), so a duplicate
    scheduler tick is recorded as `skipped` instead of double-fetching.
    """

    __tablename__ = "ingestion_jobs"

    source: Mapped[SourceType] = mapped_column(pg_enum(SourceType, name="source_type"), nullable=False)
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="SET NULL")
    )
    job_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.QUEUED
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ingestion_jobs_status_created_at", "status", "created_at"),
        Index("ix_ingestion_jobs_source", "source"),
    )


class AIProcessingJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_processing_jobs"

    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.QUEUED
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(120))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    mentions_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ai_processing_jobs_review_id", "review_id"),
        Index("ix_ai_processing_jobs_status_created_at", "status", "created_at"),
    )


class ModerationQueueItem(UUIDMixin, TimestampMixin, Base):
    """`history` accumulates every transition, so moderation is never lossy."""

    __tablename__ = "moderation_queue"

    review_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[ModerationReason] = mapped_column(
        pg_enum(ModerationReason, name="moderation_reason"), nullable=False
    )
    status: Mapped[ModerationStatus] = mapped_column(
        pg_enum(ModerationStatus, name="moderation_status"),
        nullable=False,
        default=ModerationStatus.OPEN,
    )
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    reporter_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        Index("ix_moderation_queue_status_severity", "status", "severity"),
        Index("ix_moderation_queue_review_id", "review_id"),
        UniqueConstraint("review_id", "reason", name="uq_moderation_queue_review_id_reason"),
    )


class EntityConflict(UUIDMixin, TimestampMixin, Base):
    """Two candidates too similar to choose between — a human decides, not a guess."""

    __tablename__ = "entity_conflicts"

    kind: Mapped[ConflictKind] = mapped_column(
        pg_enum(ConflictKind, name="conflict_kind"), nullable=False
    )
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cities.id", ondelete="SET NULL")
    )
    candidate_a: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    candidate_b: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    similarity: Mapped[float | None] = mapped_column(Numeric(5, 4))
    status: Mapped[ConflictStatus] = mapped_column(
        pg_enum(ConflictStatus, name="conflict_status"), nullable=False, default=ConflictStatus.OPEN
    )
    auto_resolvable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_entity_conflicts_status_kind", "status", "kind"),)
