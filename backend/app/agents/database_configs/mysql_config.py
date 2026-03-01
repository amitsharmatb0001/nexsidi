"""MySQL/MariaDB database configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

MYSQL = DatabaseConfig(
    name="mysql",
    display_name="MySQL/MariaDB",
    category="relational",
    ddl_dialect="mysql",
    default_port=3306,
    supports_transactions=True,
    supports_migrations=True,
    type_mappings={
        "string": "VARCHAR(255)",
        "integer": "INT",
        "text": "TEXT",
        "boolean": "TINYINT(1)",
        "float": "DOUBLE",
        "decimal": "DECIMAL(10,2)",
        "datetime": "DATETIME",
        "date": "DATE",
        "json": "JSON",
        "uuid": "CHAR(36)",
        "binary": "BLOB",
        "bigint": "BIGINT",
    },
    connection_templates={
        "python": "mysql+aiomysql://{user}:{password}@{host}:{port}/{database}",
        "typescript": "mysql://{user}:{password}@{host}:{port}/{database}",
        "java": "jdbc:mysql://{host}:{port}/{database}?user={user}&password={password}",
        "go": "{user}:{password}@tcp({host}:{port})/{database}?parseTime=true",
        "ruby": "mysql2://{user}:{password}@{host}:{port}/{database}",
        "rust": "mysql://{user}:{password}@{host}:{port}/{database}",
    },
    index_types=("btree", "hash", "fulltext", "spatial"),
    rules=(
        "Always use the InnoDB storage engine for transactional tables; MyISAM does not "
        "support row-level locking, foreign keys, or crash recovery.",
        "Set the default character set to utf8mb4 and collation to utf8mb4_unicode_ci on "
        "every table to support the full Unicode range including emoji.",
        "Use EXPLAIN and EXPLAIN ANALYZE before deploying queries to verify the optimizer "
        "is selecting appropriate indexes and not performing full table scans.",
        "Keep VARCHAR columns under 767 bytes for InnoDB index prefix limits on older "
        "versions; prefer VARCHAR(255) with utf8mb4 to stay within the 3072-byte limit.",
        "Use UNSIGNED on integer columns that can never be negative (e.g., counters, "
        "auto-increment IDs) to double the positive range.",
        "Store UUIDs as CHAR(36) or BINARY(16) with an ordered-UUID function "
        "(UUID_TO_BIN with swap_flag=1 on MySQL 8+) to maintain index locality.",
        "Avoid SELECT * in production queries; explicitly list columns to reduce network "
        "overhead and allow covering-index optimizations.",
        "Use ON UPDATE CURRENT_TIMESTAMP for updated_at columns to automatically track "
        "row modification times without application-level code.",
        "Prefer ENUM or lookup tables for columns with a small, fixed set of values "
        "instead of free-form VARCHAR to save storage and enforce domain constraints.",
        "Partition large tables by RANGE on date columns or by HASH on tenant_id to "
        "improve query performance and simplify data retention policies.",
        "Use prepared statements or parameterized queries exclusively to prevent SQL "
        "injection; never concatenate user input into query strings.",
        "Set innodb_buffer_pool_size to 70-80% of available RAM on dedicated database "
        "servers for optimal InnoDB caching performance.",
        "Add composite indexes that match the column order in WHERE, JOIN, and ORDER BY "
        "clauses following the leftmost-prefix rule for maximum index utilization.",
        "Use generated (virtual or stored) columns for computed values that are "
        "frequently filtered or indexed, such as YEAR(created_at) or JSON_EXTRACT paths.",
    ),
    ddl_examples={
        "users_table": (
            "CREATE TABLE IF NOT EXISTS users (\n"
            "    id          CHAR(36) NOT NULL,\n"
            "    tenant_id   CHAR(36) NOT NULL,\n"
            "    email       VARCHAR(255) NOT NULL,\n"
            "    full_name   VARCHAR(255) NOT NULL,\n"
            "    metadata    JSON DEFAULT NULL,\n"
            "    is_active   TINYINT(1) NOT NULL DEFAULT 1,\n"
            "    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
            "    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP\n"
            "                ON UPDATE CURRENT_TIMESTAMP,\n"
            "    PRIMARY KEY (id),\n"
            "    UNIQUE KEY uq_users_tenant_email (tenant_id, email),\n"
            "    CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id)\n"
            "        REFERENCES tenants(id) ON DELETE CASCADE\n"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"
        ),
        "indexes": (
            "CREATE INDEX idx_users_tenant_id ON users (tenant_id) USING BTREE;\n"
            "CREATE INDEX idx_users_email ON users (email) USING BTREE;\n"
            "CREATE FULLTEXT INDEX idx_users_fullname ON users (full_name);"
        ),
        "stored_procedure": (
            "DELIMITER //\n"
            "CREATE PROCEDURE get_active_users(IN p_tenant_id CHAR(36))\n"
            "BEGIN\n"
            "    SELECT id, email, full_name, created_at\n"
            "    FROM users\n"
            "    WHERE tenant_id = p_tenant_id\n"
            "      AND is_active = 1\n"
            "    ORDER BY created_at DESC;\n"
            "END //\n"
            "DELIMITER ;"
        ),
    },
    orm_patterns={
        "fastapi": "SQLAlchemy",
        "django": "Django ORM",
        "express": "Prisma",
        "rails": "ActiveRecord",
    },
)

register_database(MYSQL)
