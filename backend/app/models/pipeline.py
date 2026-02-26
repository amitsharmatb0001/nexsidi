"""Pipeline schema models: runs, steps, checkpoints, fixer iterations.

Tables: pipeline.runs, pipeline.steps, pipeline.checkpoints, pipeline.fixer_iterations
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class PipelineRun(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_pipeline_runs_project_id", "project_id"),
        Index("ix_pipeline_runs_organization_id", "organization_id"),
        Index("ix_pipeline_runs_status", "status"),
        {"schema": "pipeline"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255), unique=True, default=None)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(50), default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_summary: Mapped[str | None] = mapped_column(Text, default=None)

    # Relationships
    steps: Mapped[list[PipelineStep]] = relationship(back_populates="run", cascade="all, delete-orphan")
    fixer_iterations: Mapped[list[FixerIteration]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PipelineStep(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "steps"
    __table_args__ = (
        Index("ix_pipeline_steps_run_id", "run_id"),
        Index("ix_pipeline_steps_organization_id", "organization_id"),
        Index("ix_pipeline_steps_agent_name", "agent_name"),
        {"schema": "pipeline"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline.runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    input_summary: Mapped[dict | None] = mapped_column(JSONB, default=None)
    output_summary: Mapped[dict | None] = mapped_column(JSONB, default=None)
    model_used: Mapped[str | None] = mapped_column(String(50), default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # Relationships
    run: Mapped[PipelineRun] = relationship(back_populates="steps")


class Checkpoint(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "checkpoints"
    __table_args__ = (
        Index("ix_pipeline_checkpoints_project_id", "project_id"),
        Index("ix_pipeline_checkpoints_organization_id", "organization_id"),
        Index("ix_pipeline_checkpoints_status", "status"),
        {"schema": "pipeline"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="CASCADE"), nullable=False
    )
    checkpoint_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    review_data: Mapped[dict | None] = mapped_column(JSONB, default=None)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="SET NULL"), default=None
    )
    feedback: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class FixerIteration(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "fixer_iterations"
    __table_args__ = (
        Index("ix_pipeline_fixer_iterations_run_id", "run_id"),
        Index("ix_pipeline_fixer_iterations_organization_id", "organization_id"),
        {"schema": "pipeline"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline.runs.id", ondelete="CASCADE"), nullable=False
    )
    iteration: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    error_report: Mapped[dict | None] = mapped_column(JSONB, default=None)
    fix_applied: Mapped[dict | None] = mapped_column(JSONB, default=None)
    model_used: Mapped[str | None] = mapped_column(String(50), default=None)
    test_result: Mapped[str | None] = mapped_column(String(20), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)

    # Relationships
    run: Mapped[PipelineRun] = relationship(back_populates="fixer_iterations")
