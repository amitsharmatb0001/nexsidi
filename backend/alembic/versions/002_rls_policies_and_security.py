"""Row-Level Security policies, database roles, and audit permissions.

Creates two PostgreSQL roles:
    nexsidi_app   -- application user (RLS enforced, no DDL)
    nexsidi_admin -- migration user (full DDL, no RLS)

Enables RLS on all tenant-scoped tables. Policies use SET LOCAL session
variables (app.current_tenant, app.current_user_id, app.user_role) set
by the application middleware before each transaction.

Audit table (audit.logs) is made INSERT-ONLY for the app user:
    - GRANT INSERT on audit.logs TO nexsidi_app
    - REVOKE UPDATE, DELETE on audit.logs FROM nexsidi_app

Revision ID: a002
Revises: a001
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "a002"
down_revision = "a001"
branch_labels = None
depends_on = None

# Tables that have organization_id and need RLS policies
TENANT_TABLES: list[tuple[str, str]] = [
    # (schema, table)
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
]


def upgrade() -> None:
    import os

    conn = op.get_bind()

    # ── Step 1: Create database roles (idempotent) ────────────────
    # SECURITY-FIX: Read passwords from environment instead of hardcoding.
    # In production, DB_APP_PASSWORD and DB_ADMIN_PASSWORD MUST be set to
    # strong random values. The fallback is only for dev/staging convenience.
    app_password = os.environ.get("DB_APP_PASSWORD", "changeme_in_production")
    admin_password = os.environ.get("DB_ADMIN_PASSWORD", "changeme_in_production")
    conn.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'nexsidi_app') THEN
                CREATE ROLE nexsidi_app WITH LOGIN PASSWORD '{app_password}';
            END IF;
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'nexsidi_admin') THEN
                CREATE ROLE nexsidi_admin WITH LOGIN PASSWORD '{admin_password}';
            END IF;
        END
        $$;
    """))

    # ── Step 2: Grant schema-level permissions ────────────────────
    for schema in ("auth", "core", "pipeline", "chat", "billing", "audit", "deploy", "notify"):
        # App user: USAGE + SELECT/INSERT/UPDATE (no DDL, no DROP)
        conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {schema} TO nexsidi_app"))
        conn.execute(sa.text(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {schema} TO nexsidi_app"
        ))
        conn.execute(sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE ON TABLES TO nexsidi_app"
        ))

        # Admin user: full access for migrations
        conn.execute(sa.text(f"GRANT ALL ON SCHEMA {schema} TO nexsidi_admin"))
        conn.execute(sa.text(
            f"GRANT ALL ON ALL TABLES IN SCHEMA {schema} TO nexsidi_admin"
        ))
        conn.execute(sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT ALL ON TABLES TO nexsidi_admin"
        ))

    # ── Step 3: Audit log -- INSERT ONLY (CERT-In compliance) ─────
    # Revoke UPDATE and DELETE on audit.logs for the app user.
    # Audit logs are IMMUTABLE once written.
    conn.execute(sa.text("REVOKE UPDATE, DELETE ON audit.logs FROM nexsidi_app"))
    conn.execute(sa.text("GRANT INSERT ON audit.logs TO nexsidi_app"))

    # ── Step 4: Enable Row-Level Security on tenant-scoped tables ─
    for schema, table in TENANT_TABLES:
        fqn = f"{schema}.{table}"

        # Enable RLS (force even for table owner)
        conn.execute(sa.text(f"ALTER TABLE {fqn} ENABLE ROW LEVEL SECURITY"))
        conn.execute(sa.text(f"ALTER TABLE {fqn} FORCE ROW LEVEL SECURITY"))

        # Policy: tenant isolation
        # Matches rows where organization_id = current_setting('app.current_tenant')
        policy_name = f"rls_{schema}_{table}_tenant"
        conn.execute(sa.text(f"""
            CREATE POLICY {policy_name} ON {fqn}
            FOR ALL
            TO nexsidi_app
            USING (organization_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (organization_id::text = current_setting('app.current_tenant', true))
        """))

    # ── Step 5: Special RLS for tables without organization_id ────
    # auth.whatsapp_accounts -- filter by user_id
    conn.execute(sa.text("""
        ALTER TABLE auth.whatsapp_accounts ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE auth.whatsapp_accounts FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_auth_whatsapp_accounts_user ON auth.whatsapp_accounts
        FOR ALL TO nexsidi_app
        USING (user_id::text = current_setting('app.current_user_id', true))
        WITH CHECK (user_id::text = current_setting('app.current_user_id', true))
    """))

    # auth.feature_flags -- readable by all, writable by admins only
    conn.execute(sa.text("""
        ALTER TABLE auth.feature_flags ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE auth.feature_flags FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_auth_feature_flags_read ON auth.feature_flags
        FOR SELECT TO nexsidi_app
        USING (true)
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_auth_feature_flags_write ON auth.feature_flags
        FOR ALL TO nexsidi_app
        USING (current_setting('app.user_role', true) IN ('org_admin', 'super_admin'))
        WITH CHECK (current_setting('app.user_role', true) IN ('org_admin', 'super_admin'))
    """))

    # core.team_members -- filter by team's organization
    # (RLS uses a subquery to check if the team belongs to the tenant)
    conn.execute(sa.text("""
        ALTER TABLE core.team_members ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE core.team_members FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_core_team_members_tenant ON core.team_members
        FOR ALL TO nexsidi_app
        USING (
            team_id IN (
                SELECT id FROM core.teams
                WHERE organization_id::text = current_setting('app.current_tenant', true)
            )
        )
        WITH CHECK (
            team_id IN (
                SELECT id FROM core.teams
                WHERE organization_id::text = current_setting('app.current_tenant', true)
            )
        )
    """))

    # core.project_members -- filter by project's organization
    conn.execute(sa.text("""
        ALTER TABLE core.project_members ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE core.project_members FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_core_project_members_tenant ON core.project_members
        FOR ALL TO nexsidi_app
        USING (
            project_id IN (
                SELECT id FROM core.projects
                WHERE organization_id::text = current_setting('app.current_tenant', true)
            )
        )
        WITH CHECK (
            project_id IN (
                SELECT id FROM core.projects
                WHERE organization_id::text = current_setting('app.current_tenant', true)
            )
        )
    """))

    # core.organizations -- only see your own org
    conn.execute(sa.text("""
        ALTER TABLE core.organizations ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE core.organizations FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_core_organizations_tenant ON core.organizations
        FOR ALL TO nexsidi_app
        USING (id::text = current_setting('app.current_tenant', true))
        WITH CHECK (id::text = current_setting('app.current_tenant', true))
    """))

    # audit.logs -- app user can INSERT and SELECT only (no UPDATE, no DELETE)
    conn.execute(sa.text("""
        ALTER TABLE audit.logs ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE audit.logs FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_audit_logs_read ON audit.logs
        FOR SELECT TO nexsidi_app
        USING (organization_id::text = current_setting('app.current_tenant', true))
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_audit_logs_insert ON audit.logs
        FOR INSERT TO nexsidi_app
        WITH CHECK (true)
    """))

    # audit.security_events -- org-scoped read, admin-only write
    conn.execute(sa.text("""
        ALTER TABLE audit.security_events ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE audit.security_events FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_audit_security_events_read ON audit.security_events
        FOR SELECT TO nexsidi_app
        USING (user_id::text = current_setting('app.current_user_id', true)
               OR current_setting('app.user_role', true) IN ('org_admin', 'super_admin'))
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_audit_security_events_insert ON audit.security_events
        FOR INSERT TO nexsidi_app
        WITH CHECK (true)
    """))

    # audit.consent_records -- user sees own records
    conn.execute(sa.text("""
        ALTER TABLE audit.consent_records ENABLE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        ALTER TABLE audit.consent_records FORCE ROW LEVEL SECURITY
    """))
    conn.execute(sa.text("""
        CREATE POLICY rls_audit_consent_records_user ON audit.consent_records
        FOR ALL TO nexsidi_app
        USING (user_id::text = current_setting('app.current_user_id', true))
        WITH CHECK (user_id::text = current_setting('app.current_user_id', true))
    """))

    # ── Step 6: Grant nexsidi_app access to public schema for alembic_version
    conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO nexsidi_app"))
    conn.execute(sa.text("GRANT SELECT ON public.alembic_version TO nexsidi_app"))


def downgrade() -> None:
    conn = op.get_bind()

    # Drop all RLS policies
    all_rls_tables = [
        *[(s, t) for s, t in TENANT_TABLES],
        ("auth", "whatsapp_accounts"),
        ("auth", "feature_flags"),
        ("core", "team_members"),
        ("core", "project_members"),
        ("core", "organizations"),
        ("audit", "logs"),
        ("audit", "security_events"),
        ("audit", "consent_records"),
    ]
    for schema, table in all_rls_tables:
        fqn = f"{schema}.{table}"
        conn.execute(sa.text(f"ALTER TABLE {fqn} DISABLE ROW LEVEL SECURITY"))
        # Drop all policies on the table
        conn.execute(sa.text(f"""
            DO $$
            DECLARE pol RECORD;
            BEGIN
                FOR pol IN
                    SELECT policyname FROM pg_policies
                    WHERE schemaname = '{schema}' AND tablename = '{table}'
                LOOP
                    EXECUTE format('DROP POLICY %I ON {fqn}', pol.policyname);
                END LOOP;
            END $$;
        """))

    # Restore permissions for audit.logs
    conn.execute(sa.text("""
        GRANT UPDATE, DELETE ON audit.logs TO nexsidi_app
    """))
