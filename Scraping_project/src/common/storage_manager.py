import logging
from typing import Any, Optional

from src.common.config_manager import (
    ConfigManager,
    DatabaseConfig,
    DeltaLakeConfig,
    RedisConfig,
)

logger = logging.getLogger(__name__)

class StorageManager:

    _instance: Optional["StorageManager"] = None

    def __init__(self, config: ConfigManager | None = None, lazy_init: bool = True):
        self.config = config or ConfigManager.get_instance()
        self._lazy_init = lazy_init

        self._delta_instance: Any = None
        self._postgres_instance: Any = None
        self._redis_instance: Any = None

        if not lazy_init:
            self._ensure_delta()
            self._ensure_postgres()
            self._ensure_redis()

    @classmethod
    def get_instance(cls, reset: bool = False) -> "StorageManager":
        if cls._instance is None or reset:
            cls._instance = cls()
        return cls._instance

    # ============================================================================
    # ============================================================================

    @property
    def delta(self) -> Any:
        self._ensure_delta()
        return self._delta_instance

    def _ensure_delta(self) -> None:
        if self._delta_instance is None:
            from src.common.delta_lake import DeltaLakeManager

            delta_config: DeltaLakeConfig = self.config.delta_lake
            self._delta_instance = DeltaLakeManager(
                base_path=delta_config.base_path,
                start_workers=True,
            )
            logger.info(f"Delta Lake initialized: {delta_config.base_path}")

    # ============================================================================
    # ============================================================================

    @property
    def postgres(self) -> Any:
        self._ensure_postgres()
        return self._postgres_instance

    def _ensure_postgres(self) -> None:
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
    # ============================================================================

    @property
    def redis(self) -> Any:
        self._ensure_redis()
        return self._redis_instance

    def _ensure_redis(self) -> None:
        if self._redis_instance is None:
            from src.common.redis_manager import RedisManager

            redis_config: RedisConfig = self.config.redis

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
    # ============================================================================

    def health_check(self) -> dict[str, bool]:
        health = {
            "delta": False,
            "postgres": False,
            "redis": False,
        }

        try:
            self._ensure_delta()
            if self._delta_instance is not None:
                health["delta"] = self.delta.base_path.exists()
        except Exception as e:
            logger.error(f"Delta Lake health check failed: {e}")

        try:
            self._ensure_postgres()
            if self._postgres_instance is not None:
                with self.postgres.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    health["postgres"] = cursor.fetchone()[0] == 1
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")

        try:
            self._ensure_redis()
            if self._redis_instance is not None:
                from src.common.redis_manager import RedisManager

                if isinstance(self._redis_instance, RedisManager):
                    health["redis"] = self.redis.redis.ping()
                else:
                    health["redis"] = True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")

        return health

    def close_all(self) -> None:
        logger.info("Closing all storage connections...")

        if self._delta_instance is not None:
            try:
                self._delta_instance.shutdown()
                logger.info("Delta Lake closed")
            except Exception as e:
                logger.error(f"Error closing Delta Lake: {e}")

        if self._postgres_instance is not None:
            try:
                self._postgres_instance.close()
                logger.info("PostgreSQL closed")
            except Exception as e:
                logger.error(f"Error closing PostgreSQL: {e}")

        if self._redis_instance is not None:
            try:
                if hasattr(self._redis_instance, "close"):
                    self._redis_instance.close()
                logger.info("Redis closed")
            except Exception as e:
                logger.error(f"Error closing Redis: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_all()
        return False

# ============================================================================
# ============================================================================

def get_storage() -> StorageManager:
    return StorageManager.get_instance()

def get_delta() -> Any:
    return get_storage().delta

def get_postgres() -> Any:
    return get_storage().postgres

def get_redis() -> Any:
    return get_storage().redis
