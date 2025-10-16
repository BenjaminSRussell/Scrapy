"""
StorageManager - Unified abstraction layer for all storage backends

This module provides a single interface for accessing Delta Lake, PostgreSQL, and Redis,
simplifying data access patterns across the application and enabling easy testing.

Features:
- Unified interface for all storage operations
- Automatic connection management and pooling
- Consistent error handling
- Easy mocking for testing
- Lazy initialization of backends

Usage:
    from src.common.storage_manager import StorageManager

    storage = StorageManager.get_instance()

    # Delta Lake operations
    storage.delta.write_batch('stage1_discovery', records)
    records = storage.delta.read_table('stage1_discovery')

    # PostgreSQL operations
    storage.postgres.log_error('spider_name', error_details)
    metrics = storage.postgres.get_performance_metrics()

    # Redis operations
    storage.redis.mark_url_seen('https://example.com')
    is_seen = storage.redis.is_url_seen('https://example.com')
"""

import logging
from typing import Any, Optional

from src.common.config_manager import ConfigManager, DatabaseConfig, DeltaLakeConfig, RedisConfig

logger = logging.getLogger(__name__)


class StorageManager:
    """
    Unified storage manager providing access to Delta Lake, PostgreSQL, and Redis.

    This class acts as a facade over the individual storage backends, providing:
    1. Lazy initialization (backends only created when first used)
    2. Singleton pattern for resource efficiency
    3. Consistent interface across all storage types
    4. Centralized configuration management
    """

    _instance: Optional["StorageManager"] = None

    def __init__(self, config: ConfigManager | None = None, lazy_init: bool = True):
        """
        Initialize storage manager.

        Args:
            config: ConfigManager instance (uses singleton if None)
            lazy_init: If True, backends are created on first use. If False, all backends
                      are initialized immediately.
        """
        self.config = config or ConfigManager.get_instance()
        self._lazy_init = lazy_init

        # Backend instances (lazy-loaded)
        self._delta_instance: Any = None
        self._postgres_instance: Any = None
        self._redis_instance: Any = None

        # Initialize immediately if not lazy
        if not lazy_init:
            self._ensure_delta()
            self._ensure_postgres()
            self._ensure_redis()

    @classmethod
    def get_instance(cls, reset: bool = False) -> "StorageManager":
        """
        Get singleton instance of StorageManager.

        Args:
            reset: If True, creates a new instance

        Returns:
            StorageManager instance
        """
        if cls._instance is None or reset:
            cls._instance = cls()
        return cls._instance

    # ============================================================================
    # Delta Lake Access
    # ============================================================================

    @property
    def delta(self) -> Any:
        """
        Get Delta Lake manager instance.

        Returns:
            DeltaLakeManager instance
        """
        self._ensure_delta()
        return self._delta_instance

    def _ensure_delta(self) -> None:
        """Ensure Delta Lake manager is initialized."""
        if self._delta_instance is None:
            from src.common.delta_lake import DeltaLakeManager

            delta_config: DeltaLakeConfig = self.config.delta_lake
            self._delta_instance = DeltaLakeManager(
                base_path=delta_config.base_path,
                start_workers=True,
            )
            logger.info(f"Delta Lake initialized: {delta_config.base_path}")

    # ============================================================================
    # PostgreSQL Access
    # ============================================================================

    @property
    def postgres(self) -> Any:
        """
        Get PostgreSQL manager instance.

        Returns:
            PostgresManager instance
        """
        self._ensure_postgres()
        return self._postgres_instance

    def _ensure_postgres(self) -> None:
        """Ensure PostgreSQL manager is initialized."""
        if self._postgres_instance is None:
            try:
                from src.common.postgres_manager import PostgresManager

                db_config: DatabaseConfig = self.config.database
                self._postgres_instance = PostgresManager(
                    host=db_config.host,
                    port=db_config.port,
                    database=db_config.database,
                    user=db_config.user,
                    password=db_config.password,
                    min_conn=db_config.min_connections,
                    max_conn=db_config.max_connections,
                )
                logger.info(f"PostgreSQL initialized: {db_config.host}:{db_config.port}/{db_config.database}")
            except ImportError:
                logger.warning("PostgreSQL support not available (psycopg2 not installed)")
                self._postgres_instance = None
            except Exception as e:
                logger.error(f"Failed to initialize PostgreSQL: {e}")
                self._postgres_instance = None

    # ============================================================================
    # Redis Access
    # ============================================================================

    @property
    def redis(self) -> Any:
        """
        Get Redis manager instance.

        Returns:
            RedisManager instance
        """
        self._ensure_redis()
        return self._redis_instance

    def _ensure_redis(self) -> None:
        """Ensure Redis manager is initialized."""
        if self._redis_instance is None:
            from src.common.redis_manager import RedisManager

            redis_config: RedisConfig = self.config.redis

            # Handle fakeredis for testing
            if redis_config.is_fakeredis:
                import fakeredis

                self._redis_instance = fakeredis.FakeStrictRedis(decode_responses=True)
                logger.info("Redis initialized: fakeredis (testing mode)")
            else:
                self._redis_instance = RedisManager(
                    host=redis_config.host,
                    port=redis_config.port,
                    db=redis_config.db,
                    password=redis_config.password,
                    max_connections=redis_config.max_connections,
                )
                logger.info(f"Redis initialized: {redis_config.host}:{redis_config.port}")

    # ============================================================================
    # Convenience Methods
    # ============================================================================

    def health_check(self) -> dict[str, bool]:
        """
        Check health of all storage backends.

        Returns:
            Dictionary with health status of each backend
        """
        health = {
            "delta": False,
            "postgres": False,
            "redis": False,
        }

        # Check Delta Lake
        try:
            # Ensure delta is initialized
            self._ensure_delta()
            if self._delta_instance is not None:
                # Delta Lake is file-based, just check if path exists
                health["delta"] = self.delta.base_path.exists()
        except Exception as e:
            logger.error(f"Delta Lake health check failed: {e}")

        # Check PostgreSQL
        try:
            # Ensure postgres is initialized
            self._ensure_postgres()
            if self._postgres_instance is not None:
                with self.postgres.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    health["postgres"] = cursor.fetchone()[0] == 1
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")

        # Check Redis
        try:
            # Ensure redis is initialized
            self._ensure_redis()
            if self._redis_instance is not None:
                from src.common.redis_manager import RedisManager
                if isinstance(self._redis_instance, RedisManager):
                    health["redis"] = self.redis.redis.ping()
                else:
                    # FakeRedis
                    health["redis"] = True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")

        return health

    def close_all(self) -> None:
        """Close all storage connections gracefully."""
        logger.info("Closing all storage connections...")

        # Close Delta Lake
        if self._delta_instance is not None:
            try:
                self._delta_instance.shutdown()
                logger.info("Delta Lake closed")
            except Exception as e:
                logger.error(f"Error closing Delta Lake: {e}")

        # Close PostgreSQL
        if self._postgres_instance is not None:
            try:
                self._postgres_instance.close()
                logger.info("PostgreSQL closed")
            except Exception as e:
                logger.error(f"Error closing PostgreSQL: {e}")

        # Close Redis
        if self._redis_instance is not None:
            try:
                if isinstance(self._redis_instance, RedisManager):
                    self._redis_instance.close()
                logger.info("Redis closed")
            except Exception as e:
                logger.error(f"Error closing Redis: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes all connections."""
        self.close_all()
        return False


# ============================================================================
# Convenience Functions
# ============================================================================


def get_storage() -> StorageManager:
    """Get StorageManager singleton instance."""
    return StorageManager.get_instance()


def get_delta() -> Any:
    """Get Delta Lake manager directly."""
    return get_storage().delta


def get_postgres() -> Any:
    """Get PostgreSQL manager directly."""
    return get_storage().postgres


def get_redis() -> Any:
    """Get Redis manager directly."""
    return get_storage().redis
