"""Redis / Valkey key-value store configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

REDIS = DatabaseConfig(
    name="redis",
    display_name="Redis/Valkey",
    category="key_value",
    ddl_dialect="none",
    default_port=6379,
    supports_transactions=False,
    supports_migrations=False,
    type_mappings={
        "string": "String",
        "integer": "String",
        "text": "String",
        "boolean": "String",
        "float": "String",
        "decimal": "String",
        "datetime": "String",
        "date": "String",
        "json": "JSON",
        "uuid": "String",
        "binary": "String",
        "bigint": "String",
    },
    connection_templates={
        "python": "redis://{user}:{password}@{host}:{port}/{database}",
        "typescript": "redis://{user}:{password}@{host}:{port}/{database}",
        "java": "redis://{user}:{password}@{host}:{port}/{database}",
        "go": "redis://{user}:{password}@{host}:{port}/{database}",
    },
    index_types=("hash_index", "sorted_set_index", "search_index"),
    rules=(
        "Design keys with a hierarchical colon-delimited namespace: "
        "{tenant}:{entity}:{id}:{field} (e.g. t:abc:user:42:profile) for "
        "predictable scanning and KEYS/SCAN pattern matching.",
        "Always set a TTL (EXPIRE / PEXPIRE) on cache entries and ephemeral data; "
        "keys without TTL accumulate silently and cause OOM evictions.",
        "Use Hashes (HSET/HGET) for object storage instead of serialised JSON strings; "
        "Hashes allow partial reads/writes and consume less memory per field.",
        "Use Sorted Sets (ZADD/ZRANGEBYSCORE) for leaderboards, rate limiters, and "
        "time-series indexes where ordering and range queries are needed.",
        "Use Lists (LPUSH/RPOP) or Streams (XADD/XREADGROUP) for message queues; "
        "prefer Streams for consumer-group semantics and at-least-once delivery.",
        "Use Sets (SADD/SMEMBERS/SINTER) for tagging, membership checks, and set "
        "operations (union, intersection, difference) across categories.",
        "Enable RedisJSON module (JSON.SET/JSON.GET) for documents that require nested "
        "path queries instead of flattening into Hashes.",
        "Configure persistence based on durability needs: AOF (appendfsync everysec) for "
        "durable workloads, RDB snapshots for cache-only deployments, or both combined.",
        "Use MULTI/EXEC pipelines for atomic multi-key operations; note that Redis "
        "transactions are not rollback-capable -- use Lua scripts for conditional logic.",
        "Implement distributed locks with SET key value NX PX ttl (Redlock pattern) "
        "instead of SETNX; always include a TTL to prevent deadlocks.",
        "Use SCAN (not KEYS) for iterating over keyspaces in production to avoid "
        "blocking the single-threaded event loop on large datasets.",
        "Shard data across Redis Cluster hash slots by embedding {hash_tag} in key "
        "names to co-locate related keys on the same node.",
        "Use Pub/Sub or Streams for real-time event distribution; prefer Streams when "
        "consumers need replay capability and message acknowledgement.",
        "Monitor memory usage with INFO memory and set maxmemory-policy to "
        "allkeys-lru or volatile-lru to handle eviction gracefully.",
    ),
    ddl_examples={
        "key_schema_design": (
            "# Key naming convention\n"
            "#   {tenant}:user:{id}              -> Hash (profile fields)\n"
            "#   {tenant}:user:{id}:sessions     -> Set  (active session tokens)\n"
            "#   {tenant}:user:by_email          -> Hash (email -> user_id lookup)\n"
            "#   {tenant}:feed:{id}              -> Sorted Set (score = timestamp)\n"
            "#\n"
            "# Example:\n"
            "HSET t:abc:user:42 email 'alice@example.com' name 'Alice' active '1'\n"
            "EXPIRE t:abc:user:42 86400\n"
            "SADD t:abc:user:42:sessions 'sess_01' 'sess_02'\n"
            "HSET t:abc:user:by_email alice@example.com 42"
        ),
        "redisearch_index": (
            "FT.CREATE idx:users ON HASH PREFIX 1 't:abc:user:'\n"
            "    SCHEMA\n"
            "        email   TAG SORTABLE\n"
            "        name    TEXT WEIGHT 2.0\n"
            "        active  TAG\n"
            "        score   NUMERIC SORTABLE;"
        ),
        "sorted_set_pattern": (
            "# Rate limiter: sliding window (60-second window, max 100 requests)\n"
            "# On each request:\n"
            "MULTI\n"
            "ZADD   t:abc:ratelimit:user:42 {now_ms} {request_id}\n"
            "ZREMRANGEBYSCORE t:abc:ratelimit:user:42 0 {now_ms - 60000}\n"
            "ZCARD  t:abc:ratelimit:user:42\n"
            "EXPIRE t:abc:ratelimit:user:42 120\n"
            "EXEC"
        ),
    },
    orm_patterns={
        "fastapi": "redis-py/aioredis",
        "express": "ioredis",
        "django": "django-redis",
    },
)

register_database(REDIS)
