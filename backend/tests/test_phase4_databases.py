"""Phase 4 tests: All database configurations (Gaps 173-182).

Covers 10 database engine configs:
- PostgreSQL, MySQL, MongoDB, SQLite, Firebase, Supabase,
  DynamoDB, Neo4j, CockroachDB, Redis

Tests are BRUTAL: structure validation, content quality, type mappings,
rule count, DDL examples, connection templates, ORM patterns, alias resolution,
frozen checks, cross-module integration with tech_stack.
"""

from __future__ import annotations

import pytest

from app.agents.database_configs import (
    DatabaseConfig,
    get_database_config,
    list_databases,
)


# ── All 10 databases ─────────────────────────────────────────────────

ALL_DATABASES = [
    "postgresql", "mysql", "mongodb", "sqlite", "firebase",
    "supabase", "dynamodb", "neo4j", "cockroachdb", "redis",
]

RELATIONAL_DBS = ["postgresql", "mysql", "sqlite", "supabase", "cockroachdb"]
NOSQL_DBS = ["mongodb", "firebase", "dynamodb", "neo4j", "redis"]

EXPECTED_DISPLAY_NAMES = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mongodb": "MongoDB",
    "sqlite": "SQLite",
    "firebase": "Firebase",
    "supabase": "Supabase",
    "dynamodb": "DynamoDB",
    "neo4j": "Neo4j",
    "cockroachdb": "CockroachDB",
    "redis": "Redis",
}

EXPECTED_CATEGORIES = {
    "postgresql": "relational",
    "mysql": "relational",
    "sqlite": "relational",
    "supabase": "relational",
    "cockroachdb": "relational",
    "mongodb": "document",
    "firebase": "document",
    "dynamodb": "key_value",
    "neo4j": "graph",
    "redis": "key_value",
}

EXPECTED_PORTS = {
    "postgresql": 5432,
    "mysql": 3306,
    "mongodb": 27017,
    "sqlite": 0,
    "firebase": 443,
    "supabase": 5432,
    "dynamodb": 443,
    "neo4j": 7687,
    "cockroachdb": 26257,
    "redis": 6379,
}


# ── TestAllDatabasesRegistered ───────────────────────────────────────


class TestAllDatabasesRegistered:
    """Verify all 10 databases are registered."""

    def test_list_returns_all_10(self):
        databases = list_databases()
        assert len(databases) >= 10, f"Expected >= 10, got {len(databases)}: {databases}"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_database_exists(self, name: str):
        config = get_database_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_database_is_frozen(self, name: str):
        config = get_database_config(name)
        with pytest.raises(Exception):
            config.name = "hacked"  # type: ignore[misc]

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_database_is_correct_type(self, name: str):
        config = get_database_config(name)
        assert isinstance(config, DatabaseConfig)


# ── TestDatabaseCategories ───────────────────────────────────────────


class TestDatabaseCategories:
    """All databases must have correct categories."""

    @pytest.mark.parametrize("name,category", EXPECTED_CATEGORIES.items())
    def test_category_correct(self, name: str, category: str):
        config = get_database_config(name)
        assert config.category == category, \
            f"{name} category is '{config.category}', expected '{category}'"


# ── TestDatabasePorts ────────────────────────────────────────────────


class TestDatabasePorts:
    """All databases must have correct default ports."""

    @pytest.mark.parametrize("name,port", EXPECTED_PORTS.items())
    def test_port_correct(self, name: str, port: int):
        config = get_database_config(name)
        assert config.default_port == port, \
            f"{name} port is {config.default_port}, expected {port}"


# ── TestDatabaseDisplayNames ─────────────────────────────────────────


class TestDatabaseDisplayNames:
    """Display names must contain the database name."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_display_name_contains_name(self, name: str):
        config = get_database_config(name)
        expected = EXPECTED_DISPLAY_NAMES.get(name, name.capitalize())
        assert expected.lower() in config.display_name.lower(), \
            f"{name} display_name '{config.display_name}' should contain '{expected}'"


# ── TestDatabaseTypeMappings ─────────────────────────────────────────


class TestDatabaseTypeMappings:
    """All databases must have complete type mappings."""

    REQUIRED_TYPES = [
        "string", "integer", "text", "boolean", "float",
        "decimal", "datetime", "date", "json", "uuid",
        "binary", "bigint",
    ]

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_minimum_10_type_mappings(self, name: str):
        config = get_database_config(name)
        assert len(config.type_mappings) >= 10, \
            f"{name} has only {len(config.type_mappings)} type mappings"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_type_mappings_are_strings(self, name: str):
        config = get_database_config(name)
        for abstract_type, db_type in config.type_mappings.items():
            assert isinstance(abstract_type, str)
            assert isinstance(db_type, str)
            assert len(db_type) > 0, f"{name} type '{abstract_type}' maps to empty string"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    @pytest.mark.parametrize("required_type", REQUIRED_TYPES)
    def test_has_required_type(self, name: str, required_type: str):
        config = get_database_config(name)
        assert required_type in config.type_mappings, \
            f"{name} missing required type mapping '{required_type}'"


# ── TestDatabaseRules ────────────────────────────────────────────────


class TestDatabaseRules:
    """Verify rules quality for all databases."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_minimum_12_rules(self, name: str):
        config = get_database_config(name)
        assert len(config.rules) >= 12, f"{name} has only {len(config.rules)} rules"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_rules_are_substantial(self, name: str):
        config = get_database_config(name)
        for i, rule in enumerate(config.rules):
            assert isinstance(rule, str)
            assert len(rule) >= 15, f"{name} rule {i} too short: '{rule[:30]}...'"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_rules_are_tuples(self, name: str):
        config = get_database_config(name)
        assert isinstance(config.rules, tuple), f"{name} rules is not tuple"


# ── TestDatabaseDDLExamples ──────────────────────────────────────────


class TestDatabaseDDLExamples:
    """Verify DDL examples for all databases."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_minimum_3_examples(self, name: str):
        config = get_database_config(name)
        assert len(config.ddl_examples) >= 3, \
            f"{name} has only {len(config.ddl_examples)} DDL examples"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_examples_are_substantial(self, name: str):
        config = get_database_config(name)
        for step, code in config.ddl_examples.items():
            assert len(code) >= 30, \
                f"{name}/{step} DDL example too short ({len(code)} chars)"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_examples_have_multiple_lines(self, name: str):
        config = get_database_config(name)
        for step, code in config.ddl_examples.items():
            lines = [l for l in code.strip().split("\n") if l.strip()]
            assert len(lines) >= 3, f"{name}/{step} has only {len(lines)} lines"


# ── TestDatabaseConnectionTemplates ──────────────────────────────────


class TestDatabaseConnectionTemplates:
    """All databases must have connection templates."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_minimum_2_connection_templates(self, name: str):
        config = get_database_config(name)
        assert len(config.connection_templates) >= 2, \
            f"{name} has only {len(config.connection_templates)} connection templates"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_connection_templates_are_strings(self, name: str):
        config = get_database_config(name)
        for lang, template in config.connection_templates.items():
            assert isinstance(lang, str)
            assert isinstance(template, str)
            assert len(template) >= 10, \
                f"{name}/{lang} connection template too short"


# ── TestDatabaseIndexTypes ───────────────────────────────────────────


class TestDatabaseIndexTypes:
    """All databases must declare supported index types."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_index_types(self, name: str):
        config = get_database_config(name)
        assert len(config.index_types) >= 1, f"{name} has no index types"
        assert isinstance(config.index_types, tuple)

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_index_types_are_strings(self, name: str):
        config = get_database_config(name)
        for idx_type in config.index_types:
            assert isinstance(idx_type, str)
            assert len(idx_type) >= 2


# ── TestDatabaseORMPatterns ──────────────────────────────────────────


class TestDatabaseORMPatterns:
    """All databases must have ORM/driver patterns."""

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_has_minimum_2_orm_patterns(self, name: str):
        config = get_database_config(name)
        assert len(config.orm_patterns) >= 2, \
            f"{name} has only {len(config.orm_patterns)} ORM patterns"

    @pytest.mark.parametrize("name", ALL_DATABASES)
    def test_orm_patterns_are_strings(self, name: str):
        config = get_database_config(name)
        for framework, orm in config.orm_patterns.items():
            assert isinstance(framework, str)
            assert isinstance(orm, str)
            assert len(orm) >= 2


# ── TestDatabaseTransactionSupport ───────────────────────────────────


class TestDatabaseTransactionSupport:
    """Relational databases should support transactions."""

    @pytest.mark.parametrize("name", RELATIONAL_DBS)
    def test_relational_supports_transactions(self, name: str):
        config = get_database_config(name)
        assert config.supports_transactions is True, \
            f"Relational DB {name} should support transactions"

    @pytest.mark.parametrize("name", RELATIONAL_DBS)
    def test_relational_supports_migrations(self, name: str):
        config = get_database_config(name)
        assert config.supports_migrations is True, \
            f"Relational DB {name} should support migrations"


# ── TestDatabaseDDLDialect ───────────────────────────────────────────


class TestDatabaseDDLDialect:
    """DDL dialect should be appropriate for the database type."""

    def test_postgresql_dialect(self):
        config = get_database_config("postgresql")
        assert config.ddl_dialect == "postgresql"

    def test_mysql_dialect(self):
        config = get_database_config("mysql")
        assert config.ddl_dialect == "mysql"

    def test_sqlite_dialect(self):
        config = get_database_config("sqlite")
        assert config.ddl_dialect == "sqlite"

    def test_supabase_dialect_is_postgresql(self):
        config = get_database_config("supabase")
        assert config.ddl_dialect == "postgresql"

    def test_cockroachdb_dialect_is_postgresql(self):
        config = get_database_config("cockroachdb")
        assert config.ddl_dialect == "postgresql"

    @pytest.mark.parametrize("name", ["mongodb", "firebase", "dynamodb", "redis"])
    def test_nosql_dialect_is_none(self, name: str):
        config = get_database_config(name)
        assert config.ddl_dialect == "none", \
            f"NoSQL DB {name} should have ddl_dialect='none'"

    def test_neo4j_dialect_is_cypher(self):
        config = get_database_config("neo4j")
        assert config.ddl_dialect == "cypher"


# ── TestDatabaseAliases ──────────────────────────────────────────────


class TestDatabaseAliases:
    """Test alias resolution for databases."""

    ALIAS_TESTS = [
        ("postgres", "postgresql"),
        ("pg", "postgresql"),
        ("psql", "postgresql"),
        ("mariadb", "mysql"),
        ("maria", "mysql"),
        ("mongo", "mongodb"),
        ("sqlite3", "sqlite"),
        ("firestore", "firebase"),
        ("firebase_firestore", "firebase"),
        ("dynamo", "dynamodb"),
        ("aws_dynamodb", "dynamodb"),
        ("neo4j_graph", "neo4j"),
        ("cockroach", "cockroachdb"),
        ("crdb", "cockroachdb"),
        ("valkey", "redis"),
    ]

    @pytest.mark.parametrize("alias,canonical", ALIAS_TESTS)
    def test_alias_resolves(self, alias: str, canonical: str):
        config = get_database_config(alias)
        assert config.name == canonical, \
            f"Alias '{alias}' resolved to '{config.name}', expected '{canonical}'"


# ── TestDatabaseSpecificContent ──────────────────────────────────────


class TestDatabaseSpecificContent:
    """Test database-specific technology mentions in rules."""

    def test_postgresql_mentions_jsonb_or_pg(self):
        config = get_database_config("postgresql")
        rules = "\n".join(config.rules).lower()
        assert "jsonb" in rules or "postgresql" in rules or "postgres" in rules

    def test_mysql_mentions_innodb_or_mysql(self):
        config = get_database_config("mysql")
        rules = "\n".join(config.rules).lower()
        assert "innodb" in rules or "mysql" in rules or "utf8" in rules

    def test_mongodb_mentions_document_or_mongo(self):
        config = get_database_config("mongodb")
        rules = "\n".join(config.rules).lower()
        assert "document" in rules or "mongo" in rules or "collection" in rules

    def test_sqlite_mentions_wal_or_sqlite(self):
        config = get_database_config("sqlite")
        rules = "\n".join(config.rules).lower()
        assert "wal" in rules or "sqlite" in rules or "pragma" in rules

    def test_firebase_mentions_firestore_or_security(self):
        config = get_database_config("firebase")
        rules = "\n".join(config.rules).lower()
        assert "firestore" in rules or "security" in rules or "collection" in rules

    def test_supabase_mentions_rls_or_supabase(self):
        config = get_database_config("supabase")
        rules = "\n".join(config.rules).lower()
        assert "rls" in rules or "supabase" in rules or "row level" in rules

    def test_dynamodb_mentions_partition_or_dynamo(self):
        config = get_database_config("dynamodb")
        rules = "\n".join(config.rules).lower()
        assert "partition" in rules or "dynamo" in rules or "key" in rules

    def test_neo4j_mentions_cypher_or_node(self):
        config = get_database_config("neo4j")
        rules = "\n".join(config.rules).lower()
        assert "cypher" in rules or "node" in rules or "relationship" in rules

    def test_cockroachdb_mentions_distributed_or_multi_region(self):
        config = get_database_config("cockroachdb")
        rules = "\n".join(config.rules).lower()
        assert "distributed" in rules or "multi" in rules or "cockroach" in rules

    def test_redis_mentions_key_or_data_structure(self):
        config = get_database_config("redis")
        rules = "\n".join(config.rules).lower()
        assert "key" in rules or "hash" in rules or "redis" in rules


# ── TestDatabaseTechStackIntegration ─────────────────────────────────


class TestDatabaseTechStackIntegration:
    """Tech stack should know about all databases."""

    TECH_STACK_DBS = [
        "postgresql", "mysql", "mongodb", "sqlite", "firebase",
        "supabase", "dynamodb", "neo4j", "cockroachdb",
    ]

    @pytest.mark.parametrize("name", TECH_STACK_DBS)
    def test_tech_stack_knows_database(self, name: str):
        from app.services.tech_stack import resolve_tech_name
        resolved = resolve_tech_name(name)
        assert resolved is not None, f"Tech stack doesn't know about '{name}'"

    def test_database_stacks_has_10_plus(self):
        from app.services.tech_stack import DATABASE_STACKS
        assert len(DATABASE_STACKS) >= 10, \
            f"Expected >= 10 databases, got {len(DATABASE_STACKS)}"
