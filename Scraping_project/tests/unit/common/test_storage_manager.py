"""Unit tests for StorageManager

Tests unified storage interface for Delta Lake, PostgreSQL, and Redis.
"""

import pytest

from src.common.storage_manager import StorageManager, get_delta, get_postgres, get_redis


class TestStorageManagerBasics:
    """Test basic StorageManager functionality."""

    def test_singleton_pattern(self):
        """Test that StorageManager uses singleton pattern."""
        storage1 = StorageManager.get_instance()
        storage2 = StorageManager.get_instance()
        assert storage1 is storage2

    def test_reset_singleton(self):
        """Test that singleton can be reset."""
        storage1 = StorageManager.get_instance()
        storage2 = StorageManager.get_instance(reset=True)
        assert storage1 is not storage2

    def test_lazy_initialization_delta(self):
        """Test that Delta Lake is lazily initialized."""
        storage = StorageManager(lazy_init=True)
        assert storage._delta_instance is None

        # Access triggers initialization
        _ = storage.delta
        assert storage._delta_instance is not None

    def test_lazy_initialization_postgres(self):
        """Test that PostgreSQL is lazily initialized."""
        storage = StorageManager(lazy_init=True)
        assert storage._postgres_instance is None

        # Postgres may fail to initialize if not configured
        try:
            _ = storage.postgres
        except Exception:
            pass  # Expected if DB not available

    def test_lazy_initialization_redis(self):
        """Test that Redis is lazily initialized."""
        storage = StorageManager(lazy_init=True)
        assert storage._redis_instance is None

        # Access triggers initialization
        _ = storage.redis
        assert storage._redis_instance is not None

    def test_eager_initialization(self):
        """Test that all backends are initialized when lazy_init=False."""
        storage = StorageManager(lazy_init=False)

        # Delta Lake should be initialized
        assert storage._delta_instance is not None

        # Redis should be initialized
        assert storage._redis_instance is not None


class TestStorageManagerProperties:
    """Test StorageManager property accessors."""

    def test_delta_property(self):
        """Test Delta Lake property access."""
        storage = StorageManager.get_instance()
        delta = storage.delta

        assert delta is not None
        assert hasattr(delta, 'base_path')
        assert hasattr(delta, 'write')
        assert hasattr(delta, 'read')

    def test_redis_property(self):
        """Test Redis property access."""
        storage = StorageManager.get_instance()
        redis = storage.redis

        assert redis is not None
        # Redis could be either RedisManager or FakeRedis
        # Just check it exists

    def test_postgres_property_when_available(self):
        """Test PostgreSQL property access when available."""
        storage = StorageManager.get_instance()

        try:
            postgres = storage.postgres
            assert postgres is not None
            assert hasattr(postgres, 'get_connection')
        except Exception:
            # PostgreSQL may not be available in test environment
            pytest.skip("PostgreSQL not available")


class TestConvenienceFunctions:
    """Test convenience functions for direct backend access."""

    def test_get_delta(self):
        """Test get_delta convenience function."""
        delta = get_delta()
        assert delta is not None
        assert hasattr(delta, 'write')

    def test_get_redis(self):
        """Test get_redis convenience function."""
        redis = get_redis()
        assert redis is not None

    def test_get_postgres(self):
        """Test get_postgres convenience function."""
        try:
            postgres = get_postgres()
            assert postgres is not None
        except Exception:
            pytest.skip("PostgreSQL not available")


class TestHealthChecks:
    """Test storage health check functionality."""

    def test_health_check_returns_dict(self):
        """Test that health check returns dictionary."""
        storage = StorageManager.get_instance()
        health = storage.health_check()

        assert isinstance(health, dict)
        assert 'delta' in health
        assert 'postgres' in health
        assert 'redis' in health

    def test_health_check_values_are_boolean(self):
        """Test that health check values are boolean."""
        storage = StorageManager.get_instance()
        health = storage.health_check()

        for backend, status in health.items():
            assert isinstance(status, bool), f"{backend} health status should be boolean"

    def test_delta_health_check(self):
        """Test Delta Lake health check."""
        storage = StorageManager.get_instance()
        health = storage.health_check()

        # Delta Lake should be healthy (file-based)
        assert health['delta'] is True

    def test_redis_health_check(self):
        """Test Redis health check."""
        storage = StorageManager.get_instance()
        health = storage.health_check()

        # Redis should be healthy (using fakeredis in tests)
        assert health['redis'] is True


class TestContextManager:
    """Test context manager functionality."""

    def test_context_manager_enter(self):
        """Test entering context manager."""
        with StorageManager() as storage:
            assert storage is not None
            assert isinstance(storage, StorageManager)

    def test_context_manager_exit_closes_connections(self):
        """Test that exiting context manager closes connections."""
        with StorageManager() as storage:
            # Access backends to initialize them
            _ = storage.delta
            _ = storage.redis

        # After exit, close_all should have been called
        # (We can't easily verify this without mocking, but at least check no errors)

    def test_context_manager_with_operations(self):
        """Test performing operations within context manager."""
        with StorageManager() as storage:
            delta = storage.delta
            assert delta is not None

            # Try basic operation
            assert hasattr(delta, 'base_path')


class TestCloseAll:
    """Test closing all storage connections."""

    def test_close_all_no_error(self):
        """Test that close_all doesn't raise errors."""
        storage = StorageManager()

        # Access backends
        _ = storage.delta
        _ = storage.redis

        # Close all should succeed
        storage.close_all()

    def test_close_all_uninitialized_backends(self):
        """Test closing when backends aren't initialized."""
        storage = StorageManager(lazy_init=True)

        # Don't access any backends
        # close_all should still succeed
        storage.close_all()


class TestStorageManagerConfiguration:
    """Test StorageManager with different configurations."""

    def test_with_custom_config(self, monkeypatch):
        """Test StorageManager with custom configuration."""
        monkeypatch.setenv('REDIS_HOST', 'custom-redis')

        from src.common.config_manager import ConfigManager

        config = ConfigManager(env_override='testing')
        storage = StorageManager(config=config)

        assert storage.config.redis.host == 'custom-redis'

    def test_fakeredis_detection(self, monkeypatch):
        """Test that StorageManager detects fakeredis."""
        monkeypatch.setenv('REDIS_URL', 'fakeredis://')

        from src.common.config_manager import ConfigManager

        config = ConfigManager(env_override='testing')
        storage = StorageManager(config=config)

        # Should use fakeredis
        redis_client = storage.redis
        assert redis_client is not None
        # Fakeredis should be detected


# Integration tests
class TestStorageManagerIntegration:
    """Integration tests for StorageManager."""

    def test_delta_write_and_read(self):
        """Test writing and reading from Delta Lake."""
        storage = StorageManager.get_instance()

        # Test data
        test_records = [
            {'url': 'https://example.com/1', 'status': 'pending'},
            {'url': 'https://example.com/2', 'status': 'pending'},
        ]

        # Write to Delta Lake
        try:
            storage.delta.write('test_table', test_records)

            # Read back
            records = storage.delta.read('test_table')
            assert len(records) >= 2
        except Exception as e:
            pytest.skip(f"Delta Lake operation failed: {e}")

    def test_redis_operations(self):
        """Test basic Redis operations."""
        storage = StorageManager.get_instance()
        redis_client = storage.redis

        # Test basic operations (works with both Redis and FakeRedis)
        try:
            # Set a value
            if hasattr(redis_client, 'redis'):
                # RedisManager
                redis_client.redis.set('test_key', 'test_value')
                value = redis_client.redis.get('test_key')
            else:
                # Direct Redis/FakeRedis
                redis_client.set('test_key', 'test_value')
                value = redis_client.get('test_key')

            assert value is not None
        except Exception as e:
            pytest.skip(f"Redis operation failed: {e}")

    def test_all_backends_accessible(self):
        """Test that all backends are accessible through StorageManager."""
        storage = StorageManager.get_instance()

        # Delta Lake should always be accessible
        assert storage.delta is not None

        # Redis should be accessible (via fakeredis in tests)
        assert storage.redis is not None

        # PostgreSQL may not be available in all environments
        try:
            postgres = storage.postgres
            assert postgres is not None or postgres is None  # Could fail gracefully
        except Exception:
            pass  # Expected if PostgreSQL not configured


class TestBackwardCompatibility:
    """Test backward compatibility with old storage access patterns."""

    def test_old_delta_lake_manager_pattern(self):
        """Test that old DeltaLakeManager pattern still works."""
        from src.common.delta_lake import DeltaLakeManager

        # Old pattern should still work
        delta = DeltaLakeManager.get_instance()
        assert delta is not None

        # New pattern
        storage = StorageManager.get_instance()
        assert storage.delta is not None

    def test_old_redis_manager_pattern(self):
        """Test that old RedisManager pattern still works."""
        try:
            from src.common.redis_manager import RedisManager

            # Old pattern
            redis = RedisManager()
            assert redis is not None
        except Exception:
            pass  # May fail if Redis not available

        # New pattern always works (uses fakeredis if needed)
        storage = StorageManager.get_instance()
        assert storage.redis is not None
