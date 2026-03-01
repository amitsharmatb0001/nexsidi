"""SQLite database configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

SQLITE = DatabaseConfig(
    name="sqlite",
    display_name="SQLite",
    category="relational",
    ddl_dialect="sqlite",
    default_port=0,
    supports_transactions=True,
    supports_migrations=True,
    type_mappings={
        "string": "TEXT",
        "integer": "INTEGER",
        "text": "TEXT",
        "boolean": "INTEGER",
        "float": "REAL",
        "decimal": "REAL",
        "datetime": "TEXT",
        "date": "TEXT",
        "json": "TEXT",
        "uuid": "TEXT",
        "binary": "BLOB",
        "bigint": "INTEGER",
    },
    connection_templates={
        "python": "sqlite+aiosqlite:///{path}",
        "typescript": "file:{path}",
        "java": "jdbc:sqlite:{path}",
        "go": "file:{path}?_journal_mode=WAL&_busy_timeout=5000",
        "ruby": "sqlite3://{path}",
        "rust": "sqlite://{path}?mode=rwc",
    },
    index_types=("btree",),
    rules=(
        "Enable WAL (Write-Ahead Logging) mode with PRAGMA journal_mode=WAL for "
        "concurrent read/write access; WAL allows readers to proceed without blocking "
        "writers and vice versa.",
        "Set PRAGMA busy_timeout=5000 (or higher) so that concurrent writers wait "
        "instead of immediately returning SQLITE_BUSY errors.",
        "Use INTEGER PRIMARY KEY for auto-incrementing row IDs; this maps directly to "
        "SQLite's internal rowid and avoids extra index overhead.",
        "Enable foreign key enforcement with PRAGMA foreign_keys=ON at the start of "
        "every connection; it is disabled by default in SQLite.",
        "Store datetimes as ISO-8601 TEXT (YYYY-MM-DDTHH:MM:SS.sssZ) to ensure "
        "consistent sorting, human readability, and compatibility with strftime().",
        "Store UUIDs as lowercase TEXT with hyphens (36 characters) for maximum "
        "interoperability; index with a standard B-tree.",
        "Use CHECK constraints for boolean columns stored as INTEGER to enforce "
        "only 0 or 1 values: CHECK (is_active IN (0, 1)).",
        "Run PRAGMA optimize periodically (e.g., on connection close) to allow SQLite "
        "to update internal statistics and improve query planner decisions.",
        "Avoid large transactions that modify thousands of rows in a single commit; "
        "batch writes into chunks of 500-1000 rows to reduce lock contention.",
        "Use WITHOUT ROWID tables for composite-primary-key lookup tables where the "
        "primary key is the only access path and the table has no BLOB columns.",
        "Set PRAGMA cache_size=-N (negative for KiB) to control the page cache; "
        "default is ~2 MB, increase for read-heavy workloads.",
        "Use UPSERT (INSERT ... ON CONFLICT DO UPDATE) instead of separate "
        "SELECT-then-INSERT patterns to reduce round trips and avoid race conditions.",
        "Create indexes explicitly for foreign key columns; unlike PostgreSQL, SQLite "
        "does not automatically create indexes on foreign key references.",
        "Use STRICT tables (SQLite 3.37+) to enforce type affinity and reject values "
        "that do not match declared column types.",
    ),
    ddl_examples={
        "users_table": (
            "CREATE TABLE IF NOT EXISTS users (\n"
            "    id          TEXT PRIMARY KEY NOT NULL,\n"
            "    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,\n"
            "    email       TEXT NOT NULL,\n"
            "    full_name   TEXT NOT NULL,\n"
            "    metadata    TEXT NOT NULL DEFAULT '{}',\n"
            "    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),\n"
            "    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),\n"
            "    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),\n"
            "    UNIQUE (tenant_id, email)\n"
            ");"
        ),
        "indexes": (
            "CREATE INDEX idx_users_tenant_id ON users (tenant_id);\n"
            "CREATE INDEX idx_users_email ON users (email);\n"
            "CREATE INDEX idx_users_active ON users (created_at DESC)\n"
            "    WHERE is_active = 1;"
        ),
        "pragmas": (
            "PRAGMA journal_mode = WAL;\n"
            "PRAGMA busy_timeout = 5000;\n"
            "PRAGMA foreign_keys = ON;\n"
            "PRAGMA synchronous = NORMAL;\n"
            "PRAGMA cache_size = -64000;\n"
            "PRAGMA temp_store = MEMORY;"
        ),
    },
    orm_patterns={
        "fastapi": "SQLAlchemy",
        "django": "Django ORM",
        "express": "Prisma",
        "rails": "ActiveRecord",
    },
)

register_database(SQLITE)
