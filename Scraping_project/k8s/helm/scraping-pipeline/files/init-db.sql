-- PostgreSQL Database Initialization Script
-- Creates scraping_pipeline database and required tables

-- Runs as the superuser (POSTGRES_USER) when the container first initialises
-- its data directory (/docker-entrypoint-initdb.d). POSTGRES_DB creates the
-- database; this script owns the schema and creates the least-privilege role
-- the application connects as (#538). It is idempotent: on an existing volume,
-- re-run it as the superuser to migrate:
--   APP_DB_USER=scrapy_app APP_DB_PASSWORD=... psql -U postgres -f docker/init-db.sql
-- The superuser is only for this script (schema changes); the app never uses it.

-- Connect to the scraping_pipeline database
\c scraping_pipeline;

-- Performance metrics table
CREATE TABLE IF NOT EXISTS performance_metrics (
    id SERIAL PRIMARY KEY,
    stage VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    urls_processed INTEGER NOT NULL,
    processing_time_seconds FLOAT NOT NULL,
    throughput FLOAT,
    worker_count INTEGER,
    memory_usage_mb FLOAT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create index on stage and timestamp for faster queries
CREATE INDEX IF NOT EXISTS idx_perf_stage_time
ON performance_metrics(stage, timestamp DESC);

-- Error logs table
CREATE TABLE IF NOT EXISTS error_logs (
    id SERIAL PRIMARY KEY,
    stage VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    url TEXT,
    error_type VARCHAR(255) NOT NULL,
    error_message TEXT,
    stack_trace TEXT,
    http_status_code INTEGER,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create index on stage and timestamp for faster queries
CREATE INDEX IF NOT EXISTS idx_error_stage_time
ON error_logs(stage, timestamp DESC);

-- Spider run stats (also created by the app when it still had DDL rights)
CREATE TABLE IF NOT EXISTS spider_stats (
    id SERIAL PRIMARY KEY,
    spider_name VARCHAR(50) NOT NULL,
    urls_processed INTEGER NOT NULL,
    errors INTEGER NOT NULL DEFAULT 0,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_spider_stats_name_time
ON spider_stats(spider_name, timestamp DESC);

-- Error analysis reports table
CREATE TABLE IF NOT EXISTS error_analysis_reports (
    id SERIAL PRIMARY KEY,
    analysis_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    total_errors_analyzed INTEGER NOT NULL,
    num_clusters INTEGER NOT NULL,
    cluster_id INTEGER NOT NULL,
    cluster_size INTEGER NOT NULL,
    cluster_percentage FLOAT NOT NULL,
    common_error_type VARCHAR(255),
    common_url_pattern TEXT,
    avg_http_status FLOAT,
    summary TEXT NOT NULL,
    recommendations TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Least-privilege application role (#538)
-- APP_DB_USER (default scrapy_app) / APP_DB_PASSWORD come from the container
-- environment (psql \getenv, psql >= 15). Without APP_DB_PASSWORD no role is
-- created and the app keeps using whatever DB_USER it is given.
-- The role may SELECT/INSERT the pipeline tables and use their sequences. It
-- cannot create, alter, drop or truncate anything, and is not a superuser.
-- ============================================================================
-- Nobody but the owner creates objects in public (default since PG 15; explicit here).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\if :{?app_user}
\else
\set app_user scrapy_app
\endif

\if :{?app_password}
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
-- Always (re)set the password so a re-run rotates it to the current env value.
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT ON performance_metrics, error_logs, spider_stats, error_analysis_reports TO %I', :'app_user') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_user') \gexec
\echo 'App role' :'app_user' 'ready (SELECT/INSERT on pipeline tables only)'
\else
\echo 'APP_DB_PASSWORD not set: no least-privilege app role created (#538)'
\endif

-- Log initialization complete
DO $$
BEGIN
    RAISE NOTICE 'Database scraping_pipeline initialized successfully';
END $$;
