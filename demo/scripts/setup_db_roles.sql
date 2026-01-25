-- ============================================================================
-- Demo Database Roles Setup
-- ============================================================================
-- This script creates two roles for defense-in-depth:
-- 1. demo_writer: Used only by the seeder script
-- 2. demo_reader: Used by the API (SELECT only, read-only transactions)
--
-- Run this as a superuser before seeding:
--   psql -U postgres -f demo/scripts/setup_db_roles.sql
-- ============================================================================

-- Create database if not exists (run separately if needed)
-- CREATE DATABASE payroll_demo;

\c payroll_demo

-- ============================================================================
-- Writer Role (for seeder only)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'demo_writer') THEN
        CREATE ROLE demo_writer WITH LOGIN PASSWORD 'demo_writer_secret';
    END IF;
END
$$;

-- Grant full permissions to writer
GRANT ALL PRIVILEGES ON DATABASE payroll_demo TO demo_writer;
GRANT ALL PRIVILEGES ON SCHEMA public TO demo_writer;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO demo_writer;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO demo_writer;

-- Future tables too
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL PRIVILEGES ON TABLES TO demo_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL PRIVILEGES ON SEQUENCES TO demo_writer;

-- ============================================================================
-- Reader Role (for API only)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'demo_reader') THEN
        CREATE ROLE demo_reader WITH LOGIN PASSWORD 'demo_reader_secret';
    END IF;
END
$$;

-- Grant connect and read-only permissions
GRANT CONNECT ON DATABASE payroll_demo TO demo_reader;
GRANT USAGE ON SCHEMA public TO demo_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO demo_reader;

-- Future tables too
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO demo_reader;

-- ============================================================================
-- Enforce read-only at session level for demo_reader
-- ============================================================================

-- Set default transaction mode to read-only for demo_reader
ALTER ROLE demo_reader SET default_transaction_read_only = on;

-- ============================================================================
-- Verification
-- ============================================================================

-- Test that reader cannot write (should fail)
-- SET ROLE demo_reader;
-- INSERT INTO demo_meta (key, value) VALUES ('test', '{}'); -- Should fail

-- Reset
-- RESET ROLE;

-- Show roles
SELECT rolname, rolcanlogin,
       (SELECT setting FROM pg_settings WHERE name = 'default_transaction_read_only')
FROM pg_roles
WHERE rolname IN ('demo_writer', 'demo_reader');

-- ============================================================================
-- Connection strings for reference
-- ============================================================================
-- Writer (seeder): postgresql://demo_writer:demo_writer_secret@localhost:5432/payroll_demo
-- Reader (API):    postgresql://demo_reader:demo_reader_secret@localhost:5432/payroll_demo
-- ============================================================================
