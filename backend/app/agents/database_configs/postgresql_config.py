"""PostgreSQL database configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

POSTGRESQL = DatabaseConfig(
    name="postgresql",
    display_name="PostgreSQL",
    category="relational",
    ddl_dialect="postgresql",
    default_port=5432,
    supports_transactions=True,
    supports_migrations=True,
    type_mappings={
        "string": "VARCHAR(255)",
        "integer": "INTEGER",
        "text": "TEXT",
        "boolean": "BOOLEAN",
        "float": "DOUBLE PRECISION",
        "decimal": "NUMERIC(10,2)",
        "datetime": "TIMESTAMPTZ",
        "date": "DATE",
        "json": "JSONB",
        "uuid": "UUID",
        "binary": "BYTEA",
        "bigint": "BIGINT",
    },
    connection_templates={
        "python": "postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
        "typescript": "postgresql://{user}:{password}@{host}:{port}/{database}",
        "java": "jdbc:postgresql://{host}:{port}/{database}?user={user}&password={password}",
        "go": "postgres://{user}:{password}@{host}:{port}/{database}?sslmode=require",
        "ruby": "postgres://{user}:{password}@{host}:{port}/{database}",
        "rust": "postgres://{user}:{password}@{host}:{port}/{database}",
    },
    index_types=("btree", "hash", "gin", "gist", "brin", "sp_gist"),
    rules=(
        "Always use JSONB instead of JSON for indexed or queried columns; JSONB is "
        "binary-stored, supports GIN indexes, and is significantly faster for reads.",
        "Use TIMESTAMPTZ (timestamp with time zone) for all temporal columns to avoid "
        "ambiguous timezone handling; store everything in UTC.",
        "Generate UUID primary keys with gen_random_uuid() (PG 13+) instead of serial "
        "integers for distributed-safe, non-enumerable identifiers.",
        "Enable Row-Level Security (RLS) on every tenant-scoped table and create "
        "policies that filter on tenant_id set via SET LOCAL or session variables.",
        "Prefer table partitioning (RANGE on created_at or LIST on tenant_id) for "
        "tables expected to exceed 100 million rows.",
        "Use CTEs (WITH clauses) for complex multi-step queries to improve readability "
        "and allow the planner to optimize each step independently.",
        "Add CHECK constraints for domain validation (e.g., CHECK (status IN ('active', "
        "'inactive'))) rather than relying solely on application-level checks.",
        "Create partial indexes (CREATE INDEX ... WHERE condition) for queries that "
        "frequently filter on a specific subset of rows.",
        "Use BIGINT or BIGSERIAL for auto-increment IDs on high-volume tables to avoid "
        "integer overflow at 2.1 billion rows.",
        "Always define ON DELETE and ON UPDATE actions on foreign keys explicitly "
        "(CASCADE, SET NULL, RESTRICT) rather than relying on the default NO ACTION.",
        "Use EXCLUDE constraints with GiST indexes for non-overlapping range validation "
        "(e.g., scheduling, IP ranges, date ranges).",
        "Set statement_timeout and idle_in_transaction_session_timeout at the role level "
        "to prevent runaway queries and abandoned transactions from holding locks.",
        "Create covering indexes (INCLUDE clause) for index-only scans on frequently "
        "queried column combinations.",
        "Use advisory locks (pg_advisory_lock) for application-level coordination "
        "instead of SELECT ... FOR UPDATE on synthetic lock rows.",
    ),
    ddl_examples={
        "users_table": (
            "CREATE TABLE IF NOT EXISTS users (\n"
            "    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
            "    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,\n"
            "    email       VARCHAR(255) NOT NULL,\n"
            "    full_name   VARCHAR(255) NOT NULL,\n"
            "    metadata    JSONB NOT NULL DEFAULT '{}',\n"
            "    is_active   BOOLEAN NOT NULL DEFAULT TRUE,\n"
            "    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
            "    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
            "    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email)\n"
            ");"
        ),
        "indexes": (
            "CREATE INDEX idx_users_tenant_id ON users USING btree (tenant_id);\n"
            "CREATE INDEX idx_users_email ON users USING btree (email);\n"
            "CREATE INDEX idx_users_metadata ON users USING gin (metadata);\n"
            "CREATE INDEX idx_users_active ON users (created_at DESC)\n"
            "    WHERE is_active = TRUE;"
        ),
        "rls_policy": (
            "ALTER TABLE users ENABLE ROW LEVEL SECURITY;\n"
            "\n"
            "CREATE POLICY tenant_isolation ON users\n"
            "    USING (tenant_id = current_setting('app.current_tenant')::UUID);\n"
            "\n"
            "CREATE POLICY tenant_insert ON users\n"
            "    FOR INSERT\n"
            "    WITH CHECK (tenant_id = current_setting('app.current_tenant')::UUID);"
        ),
    },
    orm_patterns={
        "fastapi": "SQLAlchemy",
        "django": "Django ORM",
        "express": "Prisma",
        "rails": "ActiveRecord",
    },
)

register_database(POSTGRESQL)
