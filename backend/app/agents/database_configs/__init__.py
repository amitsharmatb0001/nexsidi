"""Database configuration registry for DDL generation and schema design.

Each supported database has a ``DatabaseConfig`` that tells Dhruv how to
generate schema definitions, connection strings, and migration scripts:
- DDL syntax (CREATE TABLE vs collection schema vs Cypher)
- Data types mapping (per-database column types)
- Index strategy (B-tree, hash, GIN, compound)
- Connection string templates
- ORM/driver patterns per language

New databases are added by creating a config file (e.g. ``mysql_config.py``)
and calling ``register_database()`` at module level.

Usage::

    from app.agents.database_configs import get_database_config

    config = get_database_config("mysql")
    print(config.ddl_dialect)       # "mysql"
    print(config.default_port)      # 3306
    print(len(config.type_mappings)) # 12+
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DatabaseConfig:
    """Immutable configuration for a database engine.

    Attributes:
        name: Canonical name (e.g. ``"mysql"``, ``"mongodb"``, ``"redis"``).
        display_name: Human-readable name (e.g. ``"MySQL/MariaDB"``).
        category: Database category (``"relational"``, ``"document"``, ``"graph"``,
                  ``"key_value"``, ``"wide_column"``, ``"time_series"``).
        ddl_dialect: DDL dialect identifier (``"mysql"``, ``"postgresql"``, ``"none"``).
        default_port: Default port number.
        supports_transactions: Whether ACID transactions are supported.
        supports_migrations: Whether schema migrations are applicable.
        type_mappings: Map of abstract type name to database-specific type.
        connection_templates: Map of language to connection string template.
        index_types: Supported index types (e.g. ``["btree", "hash", "fulltext"]``).
        rules: Database-specific rules for schema generation.
        ddl_examples: Map of example name to DDL/schema snippet.
        orm_patterns: Map of language/framework to ORM pattern name.
    """

    name: str
    display_name: str
    category: str
    ddl_dialect: str
    default_port: int
    supports_transactions: bool
    supports_migrations: bool
    type_mappings: dict[str, str]
    connection_templates: dict[str, str]
    index_types: tuple[str, ...]
    rules: tuple[str, ...]
    ddl_examples: dict[str, str]
    orm_patterns: dict[str, str]


# ── Registry ────────────────────────────────────────────────────────

_DATABASE_REGISTRY: dict[str, DatabaseConfig] = {}


def register_database(config: DatabaseConfig) -> None:
    """Register a database configuration."""
    _DATABASE_REGISTRY[config.name] = config
    logger.debug("database_registered", name=config.name)


def get_database_config(name: str) -> DatabaseConfig:
    """Get database config by canonical name.

    Falls back to ``"postgresql"`` for unknown databases.
    """
    key = name.lower().replace("-", "_").replace(" ", "_")

    # Direct match
    if key in _DATABASE_REGISTRY:
        return _DATABASE_REGISTRY[key]

    # Alias resolution
    _aliases: dict[str, str] = {
        "postgres": "postgresql", "pg": "postgresql", "psql": "postgresql",
        "mariadb": "mysql", "maria": "mysql",
        "mongo": "mongodb",
        "sqlite3": "sqlite",
        "firestore": "firebase", "firebase_firestore": "firebase",
        "dynamo": "dynamodb", "aws_dynamodb": "dynamodb",
        "neo4j_graph": "neo4j", "graph": "neo4j",
        "cockroach": "cockroachdb", "crdb": "cockroachdb",
        "valkey": "redis", "redis_primary": "redis",
    }
    resolved = _aliases.get(key, key)
    if resolved in _DATABASE_REGISTRY:
        return _DATABASE_REGISTRY[resolved]

    # Fallback
    if "postgresql" in _DATABASE_REGISTRY:
        logger.warning("unknown_database_fallback", requested=name, fallback="postgresql")
        return _DATABASE_REGISTRY["postgresql"]

    raise ValueError(f"No database config for '{name}' and no postgresql fallback")


def list_databases() -> list[str]:
    """List all registered database names."""
    return sorted(_DATABASE_REGISTRY.keys())


# ── Auto-register all database configs on import ──────────────────

from app.agents.database_configs import postgresql_config as _pg  # noqa: E402, F401
from app.agents.database_configs import mysql_config as _mysql  # noqa: E402, F401
from app.agents.database_configs import mongodb_config as _mongo  # noqa: E402, F401
from app.agents.database_configs import sqlite_config as _sqlite  # noqa: E402, F401
from app.agents.database_configs import firebase_config as _firebase  # noqa: E402, F401
from app.agents.database_configs import supabase_config as _supabase  # noqa: E402, F401
from app.agents.database_configs import dynamodb_config as _dynamo  # noqa: E402, F401
from app.agents.database_configs import neo4j_config as _neo4j  # noqa: E402, F401
from app.agents.database_configs import cockroachdb_config as _crdb  # noqa: E402, F401
from app.agents.database_configs import redis_config as _redis  # noqa: E402, F401
