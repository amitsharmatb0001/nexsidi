"""Add crash recovery columns to pipeline.runs and pipeline.steps.

Adds context_snapshot (JSONB) and execution_mode to pipeline.runs so the
orchestrator can resume interrupted pipelines from the last completed step
after a server crash.  Also adds user_id to pipeline.runs.

Revision ID: a004
Revises: a003
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a004"
down_revision = "a003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- pipeline.runs: add crash recovery columns --
    op.add_column(
        "runs",
        sa.Column("context_snapshot", JSONB, nullable=True),
        schema="pipeline",
    )
    op.add_column(
        "runs",
        sa.Column("execution_mode", sa.String(20), server_default="checkpoint", nullable=False),
        schema="pipeline",
    )
    op.add_column(
        "runs",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        schema="pipeline",
    )

    # Index on status for recovery queries (find RUNNING/PAUSED on startup)
    op.create_index(
        "ix_pipeline_runs_status_recovery",
        "runs",
        ["status"],
        schema="pipeline",
        postgresql_where=sa.text("status IN ('running', 'paused')"),
    )

    # -- pipeline.steps: add stage name and output_data for recovery --
    op.add_column(
        "steps",
        sa.Column("stage", sa.String(50), nullable=True),
        schema="pipeline",
    )
    op.add_column(
        "steps",
        sa.Column("output_data", JSONB, nullable=True),
        schema="pipeline",
    )
    op.add_column(
        "steps",
        sa.Column("step_order", sa.SmallInteger(), nullable=True),
        schema="pipeline",
    )

    # -- RLS bypass for new columns (postgres on GCP) --
    # No new policies needed: existing bypass_pipeline_runs_admin etc. cover FOR ALL


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_status_recovery", table_name="runs", schema="pipeline")

    op.drop_column("runs", "context_snapshot", schema="pipeline")
    op.drop_column("runs", "execution_mode", schema="pipeline")
    op.drop_column("runs", "user_id", schema="pipeline")

    op.drop_column("steps", "stage", schema="pipeline")
    op.drop_column("steps", "output_data", schema="pipeline")
    op.drop_column("steps", "step_order", schema="pipeline")
