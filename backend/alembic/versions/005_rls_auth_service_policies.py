"""Add RLS auth-mode bypass policies for login/register operations.

When the app connects as nexsidi_app (production), FORCE ROW LEVEL
SECURITY blocks all SELECT/INSERT operations unless app.current_tenant
is set.  But auth endpoints (/register, /login) operate BEFORE
authentication -- there is no tenant context yet.

Fix: A new session variable ``app.auth_mode``.  When set to ``'true'``
(via SET LOCAL), RLS policies allow the auth endpoints to:
- SELECT auth.users (email lookup for login)
- INSERT auth.users (create user during register)
- SELECT core.organizations (slug uniqueness check during register)
- INSERT core.organizations (create org during register)
- INSERT audit.logs (audit trail for login/register)

This is transaction-scoped (SET LOCAL) so it cannot leak across
requests, and only auth endpoints set it.

Revision ID: a005
Revises: a004
Create Date: 2026-03-01
"""

from alembic import op
import sqlalchemy as sa

revision = "a005"
down_revision = "a004"
branch_labels = None
depends_on = None

# Tables that need auth-mode bypass policies
AUTH_MODE_TABLES: list[tuple[str, str, str]] = [
    # (schema, table, operations)
    ("auth", "users", "ALL"),           # SELECT for login, INSERT for register
    ("core", "organizations", "ALL"),   # SELECT for slug check, INSERT for register
    ("audit", "logs", "INSERT"),        # Audit trail for login/register
]


def upgrade() -> None:
    conn = op.get_bind()

    for schema, table, ops in AUTH_MODE_TABLES:
        fqn = f"{schema}.{table}"
        policy_name = f"rls_{schema}_{table}_auth_mode"

        # Add auth-mode bypass policy
        # When app.auth_mode = 'true', allow the operation regardless of tenant.
        # When app.auth_mode is not set (normal requests), this policy does NOT
        # match -- the existing tenant policy (from migration 002) still applies.
        #
        # PostgreSQL note: INSERT policies only support WITH CHECK (not USING).
        # SELECT policies only support USING (not WITH CHECK).
        # ALL/UPDATE/DELETE support both USING and WITH CHECK.
        if ops == "INSERT":
            conn.execute(sa.text(f"""
                CREATE POLICY {policy_name} ON {fqn}
                FOR {ops}
                TO nexsidi_app
                WITH CHECK (current_setting('app.auth_mode', true) = 'true')
            """))
        elif ops == "SELECT":
            conn.execute(sa.text(f"""
                CREATE POLICY {policy_name} ON {fqn}
                FOR {ops}
                TO nexsidi_app
                USING (current_setting('app.auth_mode', true) = 'true')
            """))
        else:
            conn.execute(sa.text(f"""
                CREATE POLICY {policy_name} ON {fqn}
                FOR {ops}
                TO nexsidi_app
                USING (current_setting('app.auth_mode', true) = 'true')
                WITH CHECK (current_setting('app.auth_mode', true) = 'true')
            """))


def downgrade() -> None:
    conn = op.get_bind()

    for schema, table, _ops in AUTH_MODE_TABLES:
        fqn = f"{schema}.{table}"
        policy_name = f"rls_{schema}_{table}_auth_mode"
        conn.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {fqn}"))
