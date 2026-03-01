"""CockroachDB distributed SQL database configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

COCKROACHDB = DatabaseConfig(
    name="cockroachdb",
    display_name="CockroachDB",
    category="relational",
    ddl_dialect="postgresql",
    default_port=26257,
    supports_transactions=True,
    supports_migrations=True,
    type_mappings={
        "string": "VARCHAR(255)",
        "integer": "INT8",
        "text": "TEXT",
        "boolean": "BOOL",
        "float": "FLOAT8",
        "decimal": "DECIMAL(10,2)",
        "datetime": "TIMESTAMPTZ",
        "date": "DATE",
        "json": "JSONB",
        "uuid": "UUID",
        "binary": "BYTES",
        "bigint": "INT8",
    },
    connection_templates={
        "python": "cockroachdb+asyncpg://{user}:{password}@{host}:{port}/{database}?sslmode=verify-full",
        "typescript": "postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=verify-full",
        "java": "jdbc:postgresql://{host}:{port}/{database}?user={user}&password={password}&sslmode=verify-full",
        "go": "postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=verify-full",
    },
    index_types=("btree", "hash", "gin", "inverted"),
    rules=(
        "Use UUID primary keys generated with gen_random_uuid() instead of SERIAL or "
        "sequences; SERIAL creates hotspots on a single range in distributed deployments.",
        "Always include an explicit PRIMARY KEY; CockroachDB creates a hidden rowid column "
        "if omitted, which causes write hotspots on a single range.",
        "Avoid INSERT ... SELECT sub-queries; CockroachDB may not push predicates into "
        "sub-selects efficiently -- use CTEs or separate statements instead.",
        "Use INT8 (64-bit) instead of INT4 for all integer columns; CockroachDB's default "
        "integer type is INT8 and mixing sizes adds implicit casts.",
        "Configure multi-region tables with REGIONAL BY ROW or GLOBAL placement to "
        "minimise cross-region latency for geo-distributed workloads.",
        "Set LOCALITY annotations on tables (REGIONAL BY TABLE IN 'region') to pin "
        "leaseholder replicas near the users who access them most.",
        "Use AS OF SYSTEM TIME '-10s' for large analytical reads to avoid contention "
        "with OLTP writes by reading from follower replicas.",
        "Prefer IMPORT INTO for bulk data loading instead of batched INSERTs; IMPORT "
        "bypasses the SQL layer and writes directly to the storage engine.",
        "Create inverted indexes (USING GIN) on JSONB columns for efficient containment "
        "queries (@>, <@) instead of scanning full documents.",
        "Use changefeed (CREATE CHANGEFEED FOR table INTO 'kafka://...') for real-time "
        "event streaming rather than polling for row changes.",
        "Set transaction retry logic at the application layer; CockroachDB uses "
        "serializable isolation by default and retries are expected under contention.",
        "Avoid cross-database references; CockroachDB does not support queries that span "
        "multiple databases -- use schemas within a single database instead.",
        "Use HASH-sharded indexes (USING HASH WITH (bucket_count = N)) for sequential "
        "key columns to distribute writes evenly across ranges.",
        "Set the gc.ttlseconds zone config appropriately for tables that need historical "
        "AS OF SYSTEM TIME reads beyond the default 25-hour window.",
    ),
    ddl_examples={
        "users_table": (
            "CREATE TABLE IF NOT EXISTS users (\n"
            "    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
            "    tenant_id   UUID NOT NULL,\n"
            "    email       VARCHAR(255) NOT NULL,\n"
            "    full_name   VARCHAR(255) NOT NULL,\n"
            "    metadata    JSONB NOT NULL DEFAULT '{}',\n"
            "    is_active   BOOL NOT NULL DEFAULT true,\n"
            "    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
            "    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
            "    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email),\n"
            "    INDEX idx_users_tenant (tenant_id) USING HASH WITH (bucket_count = 8)\n"
            ");"
        ),
        "multi_region_config": (
            "ALTER DATABASE nexsidi SET PRIMARY REGION 'us-east1';\n"
            "ALTER DATABASE nexsidi ADD REGION 'us-west1';\n"
            "ALTER DATABASE nexsidi ADD REGION 'europe-west1';\n"
            "\n"
            "ALTER TABLE users SET LOCALITY REGIONAL BY ROW;\n"
            "ALTER TABLE users ADD COLUMN crdb_region crdb_internal_region\n"
            "    NOT NULL DEFAULT 'us-east1';"
        ),
        "changefeed": (
            "CREATE CHANGEFEED FOR TABLE users, orders\n"
            "    INTO 'kafka://broker:9092?topic_prefix=cdc_'\n"
            "    WITH updated, resolved='10s',\n"
            "         format = json,\n"
            "         diff;"
        ),
    },
    orm_patterns={
        "fastapi": "SQLAlchemy+cockroachdb",
        "express": "Prisma",
        "django": "django-cockroachdb",
    },
)

register_database(COCKROACHDB)
