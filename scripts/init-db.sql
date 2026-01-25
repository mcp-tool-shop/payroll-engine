-- Initialize database with required extensions
-- This runs automatically when the container first starts

-- UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Better text search (optional, for future use)
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Comment
COMMENT ON DATABASE payroll_dev IS 'Payroll Engine Development Database';
