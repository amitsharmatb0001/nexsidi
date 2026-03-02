"""Add webhooks table for outbound event notifications.

Revision ID: 007
Revises: 006
Create Date: 2026-03-02
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret", sa.String(128), nullable=True),  # HMAC-SHA256 signing
        sa.Column("events", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer, default=0, nullable=False),
        schema="core",
    )
    op.create_index("ix_webhooks_org", "webhooks", ["organization_id"], schema="core")
    # Note: RLS is applied in migration 008_webhooks_rls.py for idempotency.

def downgrade() -> None:
    op.drop_table("webhooks", schema="core")
