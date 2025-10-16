"""PostgreSQL Manager - Database interface for performance metrics and error logging

This module provides a centralized interface for all PostgreSQL database operations,
including performance tracking, error logging, and data retrieval for ML analysis.

Environment Variables:
    DB_HOST: PostgreSQL host (default: localhost)
    DB_PORT: PostgreSQL port (default: 5432)
    DB_NAME: Database name (default: scraping_pipeline)
    DB_USER: Database user (default: postgres)
    DB_PASSWORD: Database password (required)
"""

import logging
import os
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor

    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    psycopg2 = None
    pool = None
    RealDictCursor = None

logger = logging.getLogger(__name__)


class PostgresManager:
    """Manages PostgreSQL connections and provides methods for logging and querying."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        min_conn: int = 1,
        max_conn: int = 10,
    ):
        """Initialize PostgreSQL manager with connection pool.

        Args:
            host: Database host (default: from env or localhost)
            port: Database port (default: from env or 5432)
            database: Database name (default: from env or scraping_pipeline)
            user: Database user (default: from env or postgres)
            password: Database password (default: from env, required)
            min_conn: Minimum connections in pool
            max_conn: Maximum connections in pool
        """
        if not POSTGRES_AVAILABLE:
            raise ImportError("PostgreSQL support not available. Install with: pip install psycopg2-binary")

        # Load configuration from environment or defaults
        self.host = host or os.getenv("DB_HOST", "localhost")
        self.port = port or int(os.getenv("DB_PORT", "5432"))
        self.database = database or os.getenv("DB_NAME", "scraping_pipeline")
        self.user = user or os.getenv("DB_USER", "postgres")
        self.password = password or os.getenv("DB_PASSWORD")

        if not self.password:
            raise ValueError(
                "Database password required. Set DB_PASSWORD environment variable or pass password parameter."
            )

        # Create connection pool
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                min_conn,
                max_conn,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            logger.info(f"PostgreSQL connection pool created: {self.host}:{self.port}/{self.database}")
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL connection pool: {e}")
            raise

        # Initialize database schema
        self._initialize_schema()

    @contextmanager
    def get_connection(self):
        """Context manager for getting database connections from the pool."""
        conn = None
        try:
            conn = self.connection_pool.getconn()
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise
        finally:
            if conn:
                self.connection_pool.putconn(conn)

    def _initialize_schema(self):
        """Create required tables if they don't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Performance metrics table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id SERIAL PRIMARY KEY,
                    stage VARCHAR(50) NOT NULL,
                    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
                    urls_processed INTEGER NOT NULL,
                    processing_time_seconds FLOAT NOT NULL,
                    throughput FLOAT,  -- URLs per second
                    worker_count INTEGER,
                    memory_usage_mb FLOAT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """
            )

            # Create index on stage and timestamp for faster queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_stage_time
                ON performance_metrics(stage, timestamp DESC);
            """
            )

            # Error logs table
            cursor.execute(
                """
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
            """
            )

            # Create index on stage and timestamp for faster queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_error_stage_time
                ON error_logs(stage, timestamp DESC);
            """
            )

            # Error analysis reports table
            cursor.execute(
                """
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
            """
            )

            cursor.close()
            logger.info("Database schema initialized successfully")

    def initialize_schema(self):
        """Public helper to initialize the schema (used by tests)."""
        self._initialize_schema()

    def execute(self, query: str, params: Sequence[Any] | None = None):
        """Execute an arbitrary SQL statement using the connection pool."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            cursor.close()

    def log_performance_metric(
        self,
        stage: str,
        urls_processed: int,
        processing_time_seconds: float,
        worker_count: int | None = None,
        memory_usage_mb: float | None = None,
    ):
        """Log a performance metric for a pipeline stage.

        Args:
            stage: Pipeline stage name (e.g., 'stage1', 'stage2', 'stage3')
            urls_processed: Number of URLs processed in this batch
            processing_time_seconds: Time taken to process the batch
            worker_count: Number of workers used (optional)
            memory_usage_mb: Memory usage in MB (optional)
        """
        throughput = urls_processed / processing_time_seconds if processing_time_seconds > 0 else 0

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO performance_metrics
                    (stage, urls_processed, processing_time_seconds, throughput, worker_count, memory_usage_mb)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    stage,
                    urls_processed,
                    processing_time_seconds,
                    throughput,
                    worker_count,
                    memory_usage_mb,
                ),
            )
            cursor.close()

        logger.debug(
            f"Logged performance: {stage} - {urls_processed} URLs in {processing_time_seconds:.2f}s "
            f"({throughput:.2f} URLs/sec)"
        )

    def log_error(
        self,
        stage: str,
        url: str,
        error_type: str,
        error_message: str | None = None,
        stack_trace: str | None = None,
        http_status_code: int | None = None,
        retry_count: int = 0,
    ):
        """Log an error that occurred during pipeline processing.

        Args:
            stage: Pipeline stage name (e.g., 'stage1', 'stage2', 'stage3')
            url: URL that caused the error
            error_type: Type of error (e.g., 'TimeoutError', 'HTTPError')
            error_message: Detailed error message (optional)
            stack_trace: Full stack trace (optional)
            http_status_code: HTTP status code if applicable (optional)
            retry_count: Number of times this URL was retried
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO error_logs
                    (stage, url, error_type, error_message, stack_trace, http_status_code, retry_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    stage,
                    url,
                    error_type,
                    error_message,
                    stack_trace,
                    http_status_code,
                    retry_count,
                ),
            )
            cursor.close()

        logger.debug(f"Logged error: {stage} - {error_type} at {url}")

    def get_performance_metrics(
        self,
        stage: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Retrieve performance metrics with optional filtering.

        Args:
            stage: Filter by stage name (optional)
            start_time: Filter by start timestamp (optional)
            end_time: Filter by end timestamp (optional)
            limit: Maximum number of records to return

        Returns:
            List of performance metric records
        """
        query = "SELECT * FROM performance_metrics WHERE 1=1"
        params: list[Any] = []

        if stage:
            query += " AND stage = %s"
            params.append(stage)

        if start_time:
            query += " AND timestamp >= %s"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= %s"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            results = cursor.fetchall()
            cursor.close()

        return [dict(row) for row in results]

    def get_error_logs(
        self,
        stage: str | None = None,
        error_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Retrieve error logs with optional filtering.

        Args:
            stage: Filter by stage name (optional)
            error_type: Filter by error type (optional)
            start_time: Filter by start timestamp (optional)
            end_time: Filter by end timestamp (optional)
            limit: Maximum number of records to return

        Returns:
            List of error log records
        """
        query = "SELECT * FROM error_logs WHERE 1=1"
        params: list[Any] = []

        if stage:
            query += " AND stage = %s"
            params.append(stage)

        if error_type:
            query += " AND error_type = %s"
            params.append(error_type)

        if start_time:
            query += " AND timestamp >= %s"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= %s"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            results = cursor.fetchall()
            cursor.close()

        return [dict(row) for row in results]

    def save_error_analysis(self, total_errors: int, num_clusters: int, cluster_data: list[dict[str, Any]]):
        """Save error analysis results to the database.

        Args:
            total_errors: Total number of errors analyzed
            num_clusters: Number of clusters identified
            cluster_data: List of cluster analysis results, each containing:
                - cluster_id: Cluster identifier
                - cluster_size: Number of errors in cluster
                - cluster_percentage: Percentage of total errors
                - common_error_type: Most common error type
                - common_url_pattern: Common URL pattern
                - avg_http_status: Average HTTP status code
                - summary: Plain-English summary
                - recommendations: Recommendations for fixing
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            for cluster in cluster_data:
                cursor.execute(
                    """
                    INSERT INTO error_analysis_reports
                        (total_errors_analyzed, num_clusters, cluster_id, cluster_size,
                         cluster_percentage, common_error_type, common_url_pattern,
                         avg_http_status, summary, recommendations)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        total_errors,
                        num_clusters,
                        cluster["cluster_id"],
                        cluster["cluster_size"],
                        cluster["cluster_percentage"],
                        cluster.get("common_error_type"),
                        cluster.get("common_url_pattern"),
                        cluster.get("avg_http_status"),
                        cluster["summary"],
                        cluster.get("recommendations"),
                    ),
                )

            cursor.close()

        logger.info(f"Saved error analysis: {num_clusters} clusters from {total_errors} errors")

    def close(self):
        """Close all connections in the pool."""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("PostgreSQL connection pool closed")

    # Class-level instance for singleton pattern
    _instance: "PostgresManager | None" = None

    @classmethod
    def get_instance(cls) -> "PostgresManager | None":
        """
        Get or create global PostgreSQL manager.
        Returns:
            PostgresManager instance or None if credentials not configured
        """
        if cls._instance is None:
            # Only create if password is available
            if os.getenv("DB_PASSWORD"):
                try:
                    cls._instance = cls()
                except Exception as e:
                    logger.warning(f"PostgreSQL not available: {e}")
                    return None
            else:
                logger.info("PostgreSQL disabled - DB_PASSWORD not set")
                return None
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        if cls._instance:
            cls._instance.close()
        cls._instance = None


# Module-level convenience accessor
_postgres_manager: PostgresManager | None = None


def get_postgres_manager(**kwargs) -> PostgresManager | None:
    """Return a cached PostgresManager instance, creating it on first use."""
    global _postgres_manager

    if _postgres_manager is None:
        try:
            _postgres_manager = PostgresManager(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(f"Postgres manager unavailable: {exc}")
            return None

    return _postgres_manager
