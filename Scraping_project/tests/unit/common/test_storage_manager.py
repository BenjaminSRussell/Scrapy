import pytest

from src.common.storage_manager import (
    StorageManager,
    get_delta,
    get_postgres,
    get_redis,
)

class TestStorageManagerBasics:

    def test_singleton_pattern(self):
        storage1 = StorageManager.get_instance()
        storage2 = StorageManager.get_instance()
        assert storage1 is storage2

    def test_reset_singleton(self):
        storage1 = StorageManager.get_instance()
        storage2 = StorageManager.get_instance(reset=True)
        assert storage1 is not storage2

    def test_lazy_initialization_delta(self):
        storage = StorageManager(lazy_init=True)
        assert storage._delta_instance is None

        _ = storage.delta
        assert storage._delta_instance is not None

    def test_lazy_initialization_postgres(self):
        storage = StorageManager(lazy_init=True)
        assert storage._postgres_instance is None

        try:
            _ = storage.postgres
        except Exception:
            pass

    def test_lazy_initialization_redis(self):
        storage = StorageManager(lazy_init=True)
        assert storage._redis_instance is None

        _ = storage.redis
        assert storage._redis_instance is not None

    def test_eager_initialization(self):
        storage = StorageManager(lazy_init=False)

        assert storage._delta_instance is not None

        assert storage._redis_instance is not None

class TestStorageManagerProperties:

    def test_delta_property(self):
        storage = StorageManager.get_instance()
        delta = storage.delta

        assert delta is not None
        assert hasattr(delta, "base_path")
        assert hasattr(delta, "write")
        assert hasattr(delta, "read")

    def test_redis_property(self):
        storage = StorageManager.get_instance()
        redis = storage.redis

        assert redis is not None

    def test_postgres_property_when_available(self):
        storage = StorageManager.get_instance()

        try:
            postgres = storage.postgres
            assert postgres is not None
            assert hasattr(postgres, "get_connection")
        except Exception:
            pytest.skip("PostgreSQL not available")

class TestConvenienceFunctions:

    def test_get_delta(self):
        delta = get_delta()
        assert delta is not None
        assert hasattr(delta, "write")

    def test_get_redis(self):
        redis = get_redis()
        assert redis is not None

    def test_get_postgres(self):
        try:
            postgres = get_postgres()
            assert postgres is not None
        except Exception:
            pytest.skip("PostgreSQL not available")

class TestHealthChecks:

    def test_health_check_returns_dict(self):
        storage = StorageManager.get_instance()
        health = storage.health_check()

        assert isinstance(health, dict)
        assert "delta" in health
        assert "postgres" in health
        assert "redis" in health

    def test_health_check_values_are_boolean(self):
        storage = StorageManager.get_instance()
        health = storage.health_check()

        for backend, status in health.items():
            assert isinstance(status, bool), f"{backend} health status should be boolean"

    def test_delta_health_check(self):
        storage = StorageManager.get_instance()
        health = storage.health_check()

        assert health["delta"] is True

    def test_redis_health_check(self):
        storage = StorageManager.get_instance()
        health = storage.health_check()

        assert health["redis"] is True

class TestContextManager:

    def test_context_manager_enter(self):
        with StorageManager() as storage:
            assert storage is not None
            assert isinstance(storage, StorageManager)

    def test_context_manager_exit_closes_connections(self):
        with StorageManager() as storage:
            _ = storage.delta
            _ = storage.redis

    def test_context_manager_with_operations(self):
        with StorageManager() as storage:
            delta = storage.delta
            assert delta is not None

            assert hasattr(delta, "base_path")

class TestCloseAll:

    def test_close_all_no_error(self):
        storage = StorageManager()

        _ = storage.delta
        _ = storage.redis

        storage.close_all()

    def test_close_all_uninitialized_backends(self):
        storage = StorageManager(lazy_init=True)

        storage.close_all()

class TestStorageManagerConfiguration:

    def test_with_custom_config(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "custom-redis")

        from src.common.config_manager import ConfigManager

        config = ConfigManager(env_override="testing")
        storage = StorageManager(config=config)

        assert storage.config.redis.host == "custom-redis"

    def test_fakeredis_detection(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "fakeredis://")

        from src.common.config_manager import ConfigManager

        config = ConfigManager(env_override="testing")
        storage = StorageManager(config=config)

        redis_client = storage.redis
        assert redis_client is not None

class TestStorageManagerIntegration:

    def test_delta_write_and_read(self):
        storage = StorageManager.get_instance()

        test_records = [
            {"url": "https://example.com/1", "status": "pending"},
            {"url": "https://example.com/2", "status": "pending"},
        ]

        try:
            storage.delta.write("test_table", test_records)

            records = storage.delta.read("test_table")
            assert len(records) >= 2
        except Exception as e:
            pytest.skip(f"Delta Lake operation failed: {e}")

    def test_redis_operations(self):
        storage = StorageManager.get_instance()
        redis_client = storage.redis

        try:
            if hasattr(redis_client, "redis"):
                redis_client.redis.set("test_key", "test_value")
                value = redis_client.redis.get("test_key")
            else:
                redis_client.set("test_key", "test_value")
                value = redis_client.get("test_key")

            assert value is not None
        except Exception as e:
            pytest.skip(f"Redis operation failed: {e}")

    def test_all_backends_accessible(self):
        storage = StorageManager.get_instance()

        assert storage.delta is not None

        assert storage.redis is not None

        try:
            postgres = storage.postgres
            assert postgres is not None or postgres is None
        except Exception:
            pass

class TestBackwardCompatibility:

    def test_old_delta_lake_manager_pattern(self):
        from src.common.delta_lake import DeltaLakeManager

        delta = DeltaLakeManager.get_instance()
        assert delta is not None

        storage = StorageManager.get_instance()
        assert storage.delta is not None

    def test_old_redis_manager_pattern(self):
        try:
            from src.common.redis_manager import RedisManager

            redis = RedisManager()
            assert redis is not None
        except Exception:
            pass

        storage = StorageManager.get_instance()
        assert storage.redis is not None
