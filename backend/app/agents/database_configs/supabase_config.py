"""Supabase (hosted PostgreSQL + auth + storage + realtime) configuration for DDL generation."""

from app.agents.database_configs import DatabaseConfig, register_database

SUPABASE = DatabaseConfig(
    name="supabase",
    display_name="Supabase",
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
        "typescript": "https://{project_ref}.supabase.co",
        "supabase_client": "https://{project_ref}.supabase.co  (anon key: {anon_key})",
        "pooler": "postgres://{user}:{password}@{host}:6543/{database}?pgbouncer=true",
    },
    index_types=("btree", "hash", "gin", "gist"),
    rules=(
        "Enable Row-Level Security (RLS) on every user-facing table; Supabase exposes "
        "PostgREST directly and tables without RLS are readable by any authenticated user.",
        "Reference auth.users(id) as the foreign key for user ownership columns; use "
        "auth.uid() in RLS policies to restrict rows to the currently authenticated user.",
        "Create RLS policies with USING for SELECT/UPDATE/DELETE and WITH CHECK for "
        "INSERT to enforce both read and write access control independently.",
        "Use Supabase Storage buckets with storage.objects RLS policies for file uploads; "
        "never store binary blobs directly in database columns.",
        "Enable Realtime on tables that need live subscriptions by running ALTER "
        "PUBLICATION supabase_realtime ADD TABLE <table>; only enable on tables that "
        "genuinely need push updates to reduce WAL traffic.",
        "Use Supabase Edge Functions (Deno) for server-side logic that must not be "
        "exposed to the client, such as webhook handlers and third-party API calls.",
        "Generate UUID primary keys with gen_random_uuid() to align with Supabase's "
        "default auth.users.id type and simplify foreign key relationships.",
        "Use the service_role key only in server-side code (Edge Functions, backend); "
        "never expose it in client bundles -- use the anon key with RLS instead.",
        "Leverage PostgreSQL functions (CREATE FUNCTION ... SECURITY DEFINER) exposed "
        "via supabase.rpc() for complex operations that need to bypass RLS safely.",
        "Set up database webhooks (pg_net extension) for event-driven integrations "
        "instead of polling tables or relying on client-side triggers.",
        "Use JSONB columns with GIN indexes for flexible metadata; Supabase PostgREST "
        "supports arrow operators (->>, @>) for JSONB filtering in API queries.",
        "Create a public schema for client-accessible tables and a private schema for "
        "internal tables; Supabase only exposes the public schema via the REST API.",
        "Use supabase_migrations (supabase db diff / supabase migration new) for schema "
        "changes rather than raw SQL to keep local and remote schemas in sync.",
        "Configure connection pooling via Supavisor (port 6543, pgbouncer=true) for "
        "serverless or high-concurrency workloads to avoid exhausting direct connections.",
    ),
    ddl_examples={
        "rls_policy": (
            "-- Enable RLS and create policies for tenant + user isolation\n"
            "ALTER TABLE documents ENABLE ROW LEVEL SECURITY;\n"
            "\n"
            "CREATE POLICY documents_select ON documents\n"
            "    FOR SELECT\n"
            "    USING (\n"
            "        auth.uid() = owner_id\n"
            "        OR EXISTS (\n"
            "            SELECT 1 FROM document_shares\n"
            "            WHERE document_shares.document_id = documents.id\n"
            "              AND document_shares.user_id = auth.uid()\n"
            "        )\n"
            "    );\n"
            "\n"
            "CREATE POLICY documents_insert ON documents\n"
            "    FOR INSERT\n"
            "    WITH CHECK (auth.uid() = owner_id);\n"
            "\n"
            "CREATE POLICY documents_update ON documents\n"
            "    FOR UPDATE\n"
            "    USING (auth.uid() = owner_id)\n"
            "    WITH CHECK (auth.uid() = owner_id);"
        ),
        "storage_policy": (
            "-- Create a storage bucket and secure it with RLS\n"
            "INSERT INTO storage.buckets (id, name, public)\n"
            "    VALUES ('avatars', 'avatars', false);\n"
            "\n"
            "CREATE POLICY avatar_upload ON storage.objects\n"
            "    FOR INSERT\n"
            "    WITH CHECK (\n"
            "        bucket_id = 'avatars'\n"
            "        AND auth.uid()::text = (storage.foldername(name))[1]\n"
            "    );\n"
            "\n"
            "CREATE POLICY avatar_read ON storage.objects\n"
            "    FOR SELECT\n"
            "    USING (bucket_id = 'avatars');"
        ),
        "realtime_enable": (
            "-- Enable Realtime for specific tables\n"
            "ALTER PUBLICATION supabase_realtime ADD TABLE messages;\n"
            "ALTER PUBLICATION supabase_realtime ADD TABLE notifications;\n"
            "\n"
            "-- Client subscription (TypeScript)\n"
            "-- supabase\n"
            "--   .channel('messages')\n"
            "--   .on('postgres_changes',\n"
            "--       { event: 'INSERT', schema: 'public', table: 'messages' },\n"
            "--       (payload) => handleNewMessage(payload.new)\n"
            "--   )\n"
            "--   .subscribe();"
        ),
    },
    orm_patterns={
        "nextjs": "supabase-js",
        "fastapi": "supabase-py",
        "flutter": "supabase-flutter",
        "nuxt": "supabase-js",
    },
)

register_database(SUPABASE)
