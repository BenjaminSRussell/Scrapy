-- PostgreSQL Database Initialization Script
-- Creates pipeline_metrics database and required tables

-- Note: This script runs as the postgres user when the container first starts
-- The database is created automatically by POSTGRES_DB env var, so we just need
-- to ensure the tables exist

-- Connect to the pipeline_metrics database
\c pipeline_metrics;

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

-- Grant permissions to postgres user (already owner)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;

-- Log initialization complete
DO $$
BEGIN
    RAISE NOTICE 'Database pipeline_metrics initialized successfully';
END $$;
