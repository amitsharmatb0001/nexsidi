-- NexSidi Database Initialization
-- Run as postgres superuser. Creates schemas, app user, RLS setup.

-- Create schemas
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS pipeline;
CREATE SCHEMA IF NOT EXISTS chat;
CREATE SCHEMA IF NOT EXISTS deploy;
CREATE SCHEMA IF NOT EXISTS billing;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS notify;

-- Create app user (limited permissions — no DDL, no superuser)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nexsidi_app') THEN
        CREATE ROLE nexsidi_app WITH LOGIN PASSWORD 'changeme';
    END IF;
END $$;

-- Grant schema usage + table CRUD to app user
GRANT USAGE ON SCHEMA auth, core, pipeline, chat, deploy, billing, audit, notify TO nexsidi_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth, core, pipeline, chat, deploy, billing TO nexsidi_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA notify TO nexsidi_app;

-- Audit logs: INSERT only (immutable)
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA audit TO nexsidi_app;
REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA audit FROM nexsidi_app;

-- Grant on future tables (so migrations don't break permissions)
ALTER DEFAULT PRIVILEGES IN SCHEMA auth GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA pipeline GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA chat GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA deploy GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA billing GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA notify GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit GRANT SELECT, INSERT ON TABLES TO nexsidi_app;

-- Sequences (for any serial columns)
GRANT USAGE ON ALL SEQUENCES IN SCHEMA auth, core, pipeline, chat, deploy, billing, audit, notify TO nexsidi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, core, pipeline, chat, deploy, billing, audit, notify
    GRANT USAGE ON SEQUENCES TO nexsidi_app;

-- Enable gen_random_uuid() for UUID generation
CREATE EXTENSION IF NOT EXISTS pgcrypto;
