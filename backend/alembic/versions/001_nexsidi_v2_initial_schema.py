"""NexSidi v2 initial schema -- 8 schemas, 24 tables.

Drop old v1 tables (public schema) and create the complete v2 multi-schema
architecture with UUID primary keys, timezone-aware timestamps, and
organization-scoped tenancy.

Old v1 tables dropped (public schema):
    users, projects, chats, conversations, agent_tasks, change_requests,
    code_files, uploaded_files, deployments, email_otps, payments

New v2 schemas created:
    auth     -- users, whatsapp_accounts, feature_flags
    core     -- organizations, teams, team_members, projects, project_members,
                project_files, file_uploads
    pipeline -- runs, steps, checkpoints, fixer_iterations
    chat     -- sessions, messages
    billing  -- records, token_usage, api_keys
    audit    -- logs, security_events, consent_records
    deploy   -- deployments
    notify   -- notifications

Revision ID: a001
Revises: (initial)
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a001"
down_revision = None
branch_labels = None
depends_on = None

# ── v1 tables to drop ─────────────────────────────────────────────

V1_TABLES = [
    "payments",
    "email_otps",
    "deployments",
    "uploaded_files",
    "code_files",
    "change_requests",
    "agent_tasks",
    "conversations",
    "chats",
    "projects",
    "users",
]


def upgrade() -> None:
    # ── Step 0: Drop old v1 tables (if they exist) ─────────────────
    conn = op.get_bind()
    for table_name in V1_TABLES:
        # CASCADE drops dependent objects (FKs, indexes)
        conn.execute(
            sa.text(f"DROP TABLE IF EXISTS public.{table_name} CASCADE")
        )

    # Also drop the alembic_version from old v1 migrations if present
    # (We're starting fresh with v2)
    conn.execute(
        sa.text("DELETE FROM public.alembic_version WHERE version_num NOT LIKE 'a%'")
    )

    # ── Step 1: Create schemas ─────────────────────────────────────
    for schema in ("auth", "core", "pipeline", "chat", "billing", "audit", "deploy", "notify"):
        op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    # ── Step 2: core.organizations (no FK dependencies) ────────────
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("plan", sa.String(20), server_default="free", nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        schema="core",
    )

    # ── Step 3: auth.users (FK → core.organizations) ──────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), server_default="member", nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("auth_provider", sa.String(20), server_default="email", nullable=False),
        sa.Column("google_id", sa.String(255), nullable=True),
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("passkey_credentials", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core.organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_id"),
        schema="auth",
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"], schema="auth")
    op.create_index("ix_users_email", "users", ["email"], unique=True, schema="auth")
    op.create_index(
        "ix_users_phone", "users", ["phone"], unique=True, schema="auth",
        postgresql_where=sa.text("phone IS NOT NULL"),
    )

    # ── Step 4: auth.whatsapp_accounts (FK → auth.users) ──────────
    op.create_table(
        "whatsapp_accounts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pin_hash", sa.String(255), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id"),
        schema="auth",
    )

    # ── Step 5: auth.feature_flags (FK → auth.users) ──────────────
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("flag_key", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["updated_by"], ["auth.users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("flag_key"),
        schema="auth",
    )

    # ── Step 6: core.teams (FK → core.organizations) ──────────────
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core.organizations.id"], ondelete="CASCADE"),
        schema="core",
    )
    op.create_index("ix_teams_organization_id", "teams", ["organization_id"], schema="core")

    # ── Step 7: core.team_members (FK → core.teams, auth.users) ───
    op.create_table(
        "team_members",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), server_default="member", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["core.teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        schema="core",
    )
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"], schema="core")

    # ── Step 8: core.projects ─────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), server_default="draft", nullable=False),
        sa.Column("input_source", sa.String(20), server_default="web_chat", nullable=False),
        sa.Column("architecture_contract", postgresql.JSONB(), nullable=True),
        sa.Column("complexity_score", sa.SmallInteger(), nullable=True),
        sa.Column("ai_model_tier", sa.String(20), nullable=True),
        sa.Column("execution_mode", sa.String(20), server_default="checkpoint", nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core.organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["core.teams.id"], ondelete="SET NULL"),
        schema="core",
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"], schema="core")
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"], schema="core")
    op.create_index("ix_projects_status", "projects", ["status"], schema="core")

    # ── Step 9: core.project_members ──────────────────────────────
    op.create_table(
        "project_members",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), server_default="editor", nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=True),
        sa.Column("invitation_status", sa.String(20), server_default="accepted", nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["auth.users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        schema="core",
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"], schema="core")
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"], schema="core")
    op.create_index("ix_project_members_role", "project_members", ["role"], schema="core")

    # ── Step 10: core.project_files ───────────────────────────────
    op.create_table(
        "project_files",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("generated_by", sa.String(30), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "file_path", name="uq_project_files_project_path"),
        schema="core",
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"], schema="core")
    op.create_index("ix_project_files_organization_id", "project_files", ["organization_id"], schema="core")

    # ── Step 11: core.file_uploads ────────────────────────────────
    op.create_table(
        "file_uploads",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("scan_status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="SET NULL"),
        schema="core",
    )
    op.create_index("ix_file_uploads_project_id", "file_uploads", ["project_id"], schema="core")
    op.create_index("ix_file_uploads_organization_id", "file_uploads", ["organization_id"], schema="core")

    # ── Step 12: pipeline.runs ────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("temporal_workflow_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("current_step", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("temporal_workflow_id"),
        schema="pipeline",
    )
    op.create_index("ix_pipeline_runs_project_id", "runs", ["project_id"], schema="pipeline")
    op.create_index("ix_pipeline_runs_organization_id", "runs", ["organization_id"], schema="pipeline")
    op.create_index("ix_pipeline_runs_status", "runs", ["status"], schema="pipeline")

    # ── Step 13: pipeline.steps ───────────────────────────────────
    op.create_table(
        "steps",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("input_summary", postgresql.JSONB(), nullable=True),
        sa.Column("output_summary", postgresql.JSONB(), nullable=True),
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline.runs.id"], ondelete="CASCADE"),
        schema="pipeline",
    )
    op.create_index("ix_pipeline_steps_run_id", "steps", ["run_id"], schema="pipeline")
    op.create_index("ix_pipeline_steps_organization_id", "steps", ["organization_id"], schema="pipeline")
    op.create_index("ix_pipeline_steps_agent_name", "steps", ["agent_name"], schema="pipeline")

    # ── Step 14: pipeline.checkpoints ─────────────────────────────
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_type", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("review_data", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["auth.users.id"], ondelete="SET NULL"),
        schema="pipeline",
    )
    op.create_index("ix_pipeline_checkpoints_project_id", "checkpoints", ["project_id"], schema="pipeline")
    op.create_index("ix_pipeline_checkpoints_organization_id", "checkpoints", ["organization_id"], schema="pipeline")
    op.create_index("ix_pipeline_checkpoints_status", "checkpoints", ["status"], schema="pipeline")

    # ── Step 15: pipeline.fixer_iterations ─────────────────────────
    op.create_table(
        "fixer_iterations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("iteration", sa.SmallInteger(), nullable=False),
        sa.Column("error_report", postgresql.JSONB(), nullable=True),
        sa.Column("fix_applied", postgresql.JSONB(), nullable=True),
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("test_result", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline.runs.id"], ondelete="CASCADE"),
        schema="pipeline",
    )
    op.create_index("ix_pipeline_fixer_iterations_run_id", "fixer_iterations", ["run_id"], schema="pipeline")
    op.create_index(
        "ix_pipeline_fixer_iterations_organization_id", "fixer_iterations", ["organization_id"],
        schema="pipeline",
    )

    # ── Step 16: chat.sessions ────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_name", sa.String(50), nullable=True),
        sa.Column("channel", sa.String(20), server_default="web", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        schema="chat",
    )
    op.create_index("ix_chat_sessions_organization_id", "sessions", ["organization_id"], schema="chat")
    op.create_index("ix_chat_sessions_project_id", "sessions", ["project_id"], schema="chat")
    op.create_index("ix_chat_sessions_user_id", "sessions", ["user_id"], schema="chat")

    # ── Step 17: chat.messages ────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attachments", postgresql.JSONB(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["chat.sessions.id"], ondelete="CASCADE"),
        schema="chat",
    )
    op.create_index("ix_chat_messages_organization_id", "messages", ["organization_id"], schema="chat")
    op.create_index("ix_chat_messages_session_id", "messages", ["session_id"], schema="chat")
    op.create_index("ix_chat_messages_created_at", "messages", ["created_at"], schema="chat")

    # ── Step 18: billing.records ──────────────────────────────────
    op.create_table(
        "records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_order_id", sa.String(255), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(255), nullable=True),
        sa.Column("amount_inr", sa.Numeric(10, 2), nullable=False),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("invoice_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["core.organizations.id"], ondelete="CASCADE"),
        schema="billing",
    )
    op.create_index("ix_billing_records_organization_id", "records", ["organization_id"], schema="billing")

    # ── Step 19: billing.token_usage ──────────────────────────────
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), server_default="0", nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="SET NULL"),
        schema="billing",
    )
    op.create_index("ix_billing_token_usage_user_id", "token_usage", ["user_id"], schema="billing")
    op.create_index("ix_billing_token_usage_recorded_at", "token_usage", ["recorded_at"], schema="billing")
    op.create_index(
        "ix_billing_token_usage_user_recorded", "token_usage", ["user_id", "recorded_at"],
        schema="billing",
    )

    # ── Step 20: billing.api_keys ─────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("scope", postgresql.JSONB(), server_default='["all"]', nullable=False),
        sa.Column("rate_limit", sa.Integer(), server_default="100", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        schema="billing",
    )
    op.create_index("ix_billing_api_keys_key_hash", "api_keys", ["key_hash"], unique=True, schema="billing")
    op.create_index("ix_billing_api_keys_user_id", "api_keys", ["user_id"], schema="billing")

    # ── Step 21: audit.logs (IMMUTABLE) ───────────────────────────
    op.create_table(
        "logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="audit",
    )
    op.create_index("ix_audit_logs_user_id", "logs", ["user_id"], schema="audit")
    op.create_index("ix_audit_logs_organization_id", "logs", ["organization_id"], schema="audit")
    op.create_index("ix_audit_logs_action", "logs", ["action"], schema="audit")
    op.create_index("ix_audit_logs_entity", "logs", ["entity_type", "entity_id"], schema="audit")
    op.create_index("ix_audit_logs_created_at", "logs", ["created_at"], schema="audit")

    # ── Step 22: audit.security_events ────────────────────────────
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("source_ip", postgresql.INET(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="audit",
    )
    op.create_index("ix_audit_security_events_event_type", "security_events", ["event_type"], schema="audit")
    op.create_index("ix_audit_security_events_severity", "security_events", ["severity"], schema="audit")
    op.create_index(
        "ix_audit_security_events_unresolved", "security_events", ["resolved"],
        schema="audit", postgresql_where=sa.text("resolved = false"),
    )
    op.create_index("ix_audit_security_events_created_at", "security_events", ["created_at"], schema="audit")

    # ── Step 23: audit.consent_records ────────────────────────────
    op.create_table(
        "consent_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("consent_type", sa.String(50), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        schema="audit",
    )
    op.create_index("ix_audit_consent_records_user_id", "consent_records", ["user_id"], schema="audit")
    op.create_index(
        "ix_audit_consent_records_consent_type", "consent_records", ["consent_type"],
        schema="audit",
    )

    # ── Step 24: deploy.deployments ───────────────────────────────
    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("auth_method", sa.String(20), server_default="oauth", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("deployment_url", sa.Text(), nullable=True),
        sa.Column("deploy_log", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["core.projects.id"], ondelete="CASCADE"),
        schema="deploy",
    )
    op.create_index("ix_deploy_deployments_project_id", "deployments", ["project_id"], schema="deploy")
    op.create_index("ix_deploy_deployments_organization_id", "deployments", ["organization_id"], schema="deploy")
    op.create_index("ix_deploy_deployments_status", "deployments", ["status"], schema="deploy")

    # ── Step 25: notify.notifications ─────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), server_default="web", nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        schema="notify",
    )
    op.create_index("ix_notify_notifications_user_id", "notifications", ["user_id"], schema="notify")
    op.create_index(
        "ix_notify_notifications_unread", "notifications", ["user_id"],
        schema="notify", postgresql_where=sa.text("read = false"),
    )


def downgrade() -> None:
    """Drop all v2 tables and schemas (reverse order of creation)."""
    # Tables (reverse FK dependency order)
    for table, schema in [
        ("notifications", "notify"),
        ("deployments", "deploy"),
        ("consent_records", "audit"),
        ("security_events", "audit"),
        ("logs", "audit"),
        ("api_keys", "billing"),
        ("token_usage", "billing"),
        ("records", "billing"),
        ("messages", "chat"),
        ("sessions", "chat"),
        ("fixer_iterations", "pipeline"),
        ("checkpoints", "pipeline"),
        ("steps", "pipeline"),
        ("runs", "pipeline"),
        ("file_uploads", "core"),
        ("project_files", "core"),
        ("project_members", "core"),
        ("projects", "core"),
        ("team_members", "core"),
        ("teams", "core"),
        ("feature_flags", "auth"),
        ("whatsapp_accounts", "auth"),
        ("users", "auth"),
        ("organizations", "core"),
    ]:
        op.drop_table(table, schema=schema)

    # Drop schemas
    for schema in ("notify", "deploy", "audit", "billing", "chat", "pipeline", "core", "auth"):
        op.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
