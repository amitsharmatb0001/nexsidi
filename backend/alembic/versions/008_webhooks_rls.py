"""Enable Row-Level Security on core.webhooks table.

Revision ID: 008
Revises: 007
Create Date: 2026-03-02
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # WEBHOOK-FIX: Enable RLS on core.webhooks for tenant isolation.
    # Applied as a separate migration because the initial 007 migration
    # may have been applied before RLS was added to the upgrade function.
    # All commands are idempotent via DO $$ blocks.
    conn = op.get_bind()

    conn.execute(sa.text("ALTER TABLE core.webhooks ENABLE ROW LEVEL SECURITY"))
    conn.execute(sa.text("ALTER TABLE core.webhooks FORCE ROW LEVEL SECURITY"))

    # Create policy idempotently: drop if exists, then create
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'core'
                  AND tablename = 'webhooks'
                  AND policyname = 'rls_core_webhooks_tenant'
            ) THEN
                CREATE POLICY rls_core_webhooks_tenant ON core.webhooks
                FOR ALL
                TO nexsidi_app
                USING (organization_id::text = current_setting('app.current_tenant', true))
                WITH CHECK (organization_id::text = current_setting('app.current_tenant', true));
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$
        BEGIN
            DROP POLICY IF EXISTS rls_core_webhooks_tenant ON core.webhooks;
        END
        $$;
    """))
    conn.execute(sa.text("ALTER TABLE core.webhooks DISABLE ROW LEVEL SECURITY"))
