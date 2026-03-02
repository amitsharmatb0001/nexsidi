"""Add artifact_data column to pipeline.runs for ZIP storage.

Revision ID: 006
Revises: 005
Create Date: 2026-03-02
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "a005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("artifact_data", sa.LargeBinary, nullable=True),
        schema="pipeline",
    )

def downgrade() -> None:
    op.drop_column("runs", "artifact_data", schema="pipeline")
