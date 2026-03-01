"""Neo4j graph database configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

NEO4J = DatabaseConfig(
    name="neo4j",
    display_name="Neo4j",
    category="graph",
    ddl_dialect="cypher",
    default_port=7687,
    supports_transactions=True,
    supports_migrations=False,
    type_mappings={
        "string": "String",
        "integer": "Integer",
        "text": "String",
        "boolean": "Boolean",
        "float": "Float",
        "decimal": "Float",
        "datetime": "DateTime",
        "date": "Date",
        "json": "Map",
        "uuid": "String",
        "binary": "ByteArray",
        "bigint": "Integer",
    },
    connection_templates={
        "python": "neo4j://{host}:{port}",
        "typescript": "neo4j://{host}:{port}",
        "java": "neo4j://{host}:{port}",
    },
    index_types=("btree", "text", "point", "range", "composite"),
    rules=(
        "Use PascalCase for node labels (e.g. :User, :BlogPost) and UPPER_SNAKE_CASE "
        "for relationship types (e.g. -[:CREATED_BY]->).",
        "Model relationships as first-class citizens; avoid storing foreign keys as "
        "node properties -- use typed relationships with optional properties instead.",
        "Prefer MERGE over CREATE when upserting nodes to prevent duplicate data; "
        "always include a unique constraint on the merge key.",
        "Create uniqueness constraints (CREATE CONSTRAINT ... FOR (n:Label) REQUIRE "
        "n.prop IS UNIQUE) before bulk-loading data to enforce data integrity.",
        "Use parameterised Cypher queries ($param syntax) in application code to "
        "prevent injection and allow query plan caching.",
        "Keep relationship chains short (3-4 hops) in single queries; use APOC path "
        "expander or GDS for deeper traversals to avoid combinatorial explosion.",
        "Store temporal data as native DateTime properties rather than epoch integers "
        "to leverage Neo4j temporal functions (duration, date arithmetic).",
        "Use composite indexes on node label + multiple properties for queries that "
        "filter on more than one field simultaneously.",
        "Avoid super-nodes (nodes with >100k relationships) by introducing intermediate "
        "nodes or bucketing relationships (e.g. monthly sub-nodes).",
        "Use CALL { ... } IN TRANSACTIONS for batch operations to avoid out-of-memory "
        "errors when processing millions of nodes or relationships.",
        "Leverage APOC library (apoc.periodic.iterate, apoc.load.json) for ETL and "
        "scheduled maintenance tasks instead of writing custom scripts.",
        "Design node labels to be mutually exclusive when possible; prefer single-label "
        "nodes with properties over multi-label nodes for cleaner query patterns.",
        "Set property existence constraints (REQUIRE n.prop IS NOT NULL) on critical "
        "fields to enforce data quality at the database level.",
        "Use full-text search indexes (CREATE FULLTEXT INDEX) for natural-language "
        "queries instead of CONTAINS or regular expression filters.",
    ),
    ddl_examples={
        "uniqueness_constraint": (
            "// Ensure every User node has a unique email\n"
            "CREATE CONSTRAINT user_email_unique IF NOT EXISTS\n"
            "FOR (u:User) REQUIRE u.email IS UNIQUE;\n"
            "\n"
            "// Ensure every User node has an id property\n"
            "CREATE CONSTRAINT user_id_exists IF NOT EXISTS\n"
            "FOR (u:User) REQUIRE u.id IS NOT NULL;"
        ),
        "composite_index": (
            "// Composite index for tenant-scoped queries\n"
            "CREATE INDEX user_tenant_status IF NOT EXISTS\n"
            "FOR (u:User) ON (u.tenant_id, u.status);\n"
            "\n"
            "// Full-text search index on user names\n"
            "CREATE FULLTEXT INDEX user_name_search IF NOT EXISTS\n"
            "FOR (u:User) ON EACH [u.firstName, u.lastName];"
        ),
        "cypher_query_pattern": (
            "MATCH (u:User {tenant_id: $tenantId})-[:BELONGS_TO]->(o:Organization)\n"
            "WHERE u.is_active = true\n"
            "OPTIONAL MATCH (u)-[:HAS_ROLE]->(r:Role)\n"
            "RETURN u.email AS email,\n"
            "       o.name  AS organization,\n"
            "       collect(r.name) AS roles\n"
            "ORDER BY u.created_at DESC\n"
            "LIMIT 50;"
        ),
    },
    orm_patterns={
        "fastapi": "neo4j-python",
        "express": "neo4j-driver",
    },
)

register_database(NEO4J)
