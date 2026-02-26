"""Add RLS bypass policies for Cloud SQL admin user (postgres).

GCP Cloud SQL's `postgres` user is NOT a true PostgreSQL superuser
(rolsuper=false, rolbypassrls=false). Since migration 002 uses
FORCE ROW LEVEL SECURITY, the postgres user needs explicit bypass
policies to INSERT/UPDATE/SELECT on RLS-protected tables.

Without this, the app (connecting as postgres) gets:
    "new row violates row-level security policy for table X"

Revision ID: a003
Revises: a002
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa

revision = "a003"
down_revision = "a002"
branch_labels = None
depends_on = None

# All tables that have RLS enabled (from migration 002)
ALL_RLS_TABLES: list[tuple[str, str]] = [
    # Tenant-scoped tables (organization_id)
    ("auth", "users"),
    ("core", "teams"),
    ("core", "projects"),
    ("core", "project_files"),
    ("core", "file_uploads"),
    ("pipeline", "runs"),
    ("pipeline", "steps"),
    ("pipeline", "checkpoints"),
    ("pipeline", "fixer_iterations"),
    ("chat", "sessions"),
    ("chat", "messages"),
    ("billing", "records"),
    ("billing", "token_usage"),
    ("billing", "api_keys"),
    ("deploy", "deployments"),
    ("notify", "notifications"),
    # Special RLS tables
    ("auth", "whatsapp_accounts"),
    ("auth", "feature_flags"),
    ("core", "team_members"),
    ("core", "project_members"),
    ("core", "organizations"),
    ("audit", "logs"),
    ("audit", "security_events"),
    ("audit", "consent_records"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for schema, table in ALL_RLS_TABLES:
        fqn = f"{schema}.{table}"
        policy_name = f"bypass_{schema}_{table}_admin"
        conn.execute(sa.text(f"""
            CREATE POLICY {policy_name} ON {fqn}
            FOR ALL
            TO postgres
            USING (true)
            WITH CHECK (true)
        """))

    # Also grant bypass to cloudsqlsuperuser role (GCP managed)
    for schema, table in ALL_RLS_TABLES:
        fqn = f"{schema}.{table}"
        policy_name = f"bypass_{schema}_{table}_cloudsql"
        conn.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cloudsqlsuperuser') THEN
                    EXECUTE format(
                        'CREATE POLICY {policy_name} ON {fqn} FOR ALL TO cloudsqlsuperuser USING (true) WITH CHECK (true)'
                    );
                END IF;
            END $$;
        """))


def downgrade() -> None:
    conn = op.get_bind()

    for schema, table in ALL_RLS_TABLES:
        fqn = f"{schema}.{table}"
        # Drop admin bypass policies
        conn.execute(sa.text(f"""
            DROP POLICY IF EXISTS bypass_{schema}_{table}_admin ON {fqn}
        """))
        conn.execute(sa.text(f"""
            DROP POLICY IF EXISTS bypass_{schema}_{table}_cloudsql ON {fqn}
        """))
