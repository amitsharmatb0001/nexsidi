"""Add missing columns and tables to match ORM models.

Fixes model-migration drift:
1. auth.users: add email_verified column
2. auth.whatsapp_accounts: add organization_id column + fix unique constraint
3. auth.password_reset_tokens: create table (model exists, migration missing)
4. auth.email_verification_tokens: create table (model exists, migration missing)

Revision ID: 009
Revises: 008
Create Date: 2026-03-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. auth.users: add email_verified ─────────────────────────
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="auth",
    )

    # ── 2. auth.whatsapp_accounts: add organization_id ────────────
    # TenantMixin requires organization_id for RLS
    op.add_column(
        "whatsapp_accounts",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        schema="auth",
    )
    # Backfill: set organization_id from the linked user's organization_id
    op.execute("""
        UPDATE auth.whatsapp_accounts wa
        SET organization_id = u.organization_id
        FROM auth.users u
        WHERE wa.user_id = u.id
        AND wa.organization_id IS NULL
    """)
    # Now make it NOT NULL
    op.alter_column(
        "whatsapp_accounts", "organization_id",
        nullable=False,
        schema="auth",
    )
    # Drop old single-column unique on user_id, replace with composite
    op.drop_constraint("whatsapp_accounts_user_id_key", "whatsapp_accounts", schema="auth")
    op.create_unique_constraint(
        "uq_whatsapp_user_org", "whatsapp_accounts",
        ["user_id", "organization_id"],
        schema="auth",
    )
    op.create_index(
        "ix_whatsapp_accounts_organization_id",
        "whatsapp_accounts", ["organization_id"],
        schema="auth",
    )

    # ── 3. auth.password_reset_tokens ─────────────────────────────
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash"),
        schema="auth",
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens", ["token_hash"],
        unique=True, schema="auth",
    )

    # ── 4. auth.email_verification_tokens ─────────────────────────
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash"),
        schema="auth",
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens", ["token_hash"],
        unique=True, schema="auth",
    )

    # ── 5. RLS policies for new tables ────────────────────────────
    # Password reset tokens: service-level access only (no RLS needed, accessed via token_hash)
    # Email verification tokens: same pattern
    # But enable RLS for audit compliance
    op.execute("ALTER TABLE auth.password_reset_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth.email_verification_tokens ENABLE ROW LEVEL SECURITY")

    # Allow auth service bypass (matches pattern from 005_rls_auth_service_policies)
    op.execute("""
        CREATE POLICY password_reset_tokens_auth_bypass ON auth.password_reset_tokens
        FOR ALL
        USING (current_setting('app.auth_mode', true) = 'true')
        WITH CHECK (current_setting('app.auth_mode', true) = 'true')
    """)
    op.execute("""
        CREATE POLICY email_verification_tokens_auth_bypass ON auth.email_verification_tokens
        FOR ALL
        USING (current_setting('app.auth_mode', true) = 'true')
        WITH CHECK (current_setting('app.auth_mode', true) = 'true')
    """)

    # Grant permissions to app user (matches init-db.sql pattern)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON auth.password_reset_tokens TO nexsidi_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON auth.email_verification_tokens TO nexsidi_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS email_verification_tokens_auth_bypass ON auth.email_verification_tokens")
    op.execute("DROP POLICY IF EXISTS password_reset_tokens_auth_bypass ON auth.password_reset_tokens")
    op.drop_table("email_verification_tokens", schema="auth")
    op.drop_table("password_reset_tokens", schema="auth")
    op.drop_index("ix_whatsapp_accounts_organization_id", "whatsapp_accounts", schema="auth")
    op.drop_constraint("uq_whatsapp_user_org", "whatsapp_accounts", schema="auth")
    op.create_unique_constraint("whatsapp_accounts_user_id_key", "whatsapp_accounts", ["user_id"], schema="auth")
    op.drop_column("whatsapp_accounts", "organization_id", schema="auth")
    op.drop_column("users", "email_verified", schema="auth")
