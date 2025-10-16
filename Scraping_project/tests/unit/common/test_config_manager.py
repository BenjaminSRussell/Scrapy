"""Unit tests for ConfigManager

Tests configuration loading, validation, and precedence.
"""


import pytest
import yaml

from src.common.config_manager import (
    AppConfig,
    ConfigManager,
    DatabaseConfig,
    DeltaLakeConfig,
    Environment,
    KafkaConfig,
    RedisConfig,
)


class TestConfigManagerBasics:
    """Test basic ConfigManager functionality."""

    def test_singleton_pattern(self):
        """Test that ConfigManager uses singleton pattern."""
        config1 = ConfigManager.get_instance()
        config2 = ConfigManager.get_instance()
        assert config1 is config2

    def test_reset_singleton(self):
        """Test that singleton can be reset."""
        config1 = ConfigManager.get_instance()
        config2 = ConfigManager.get_instance(reset=True)
        assert config1 is not config2

    def test_config_property(self):
        """Test that config property returns AppConfig."""
        config_manager = ConfigManager.get_instance()
        assert isinstance(config_manager.config, AppConfig)


class TestConfigManagerProperties:
    """Test ConfigManager property accessors."""

    def test_database_property(self):
        """Test database configuration access."""
        config = ConfigManager.get_instance()
        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.database.host, str)
        assert isinstance(config.database.port, int)

    def test_redis_property(self):
        """Test Redis configuration access."""
        config = ConfigManager.get_instance()
        assert isinstance(config.redis, RedisConfig)
        assert isinstance(config.redis.host, str)
        assert isinstance(config.redis.port, int)

    def test_kafka_property(self):
        """Test Kafka configuration access."""
        config = ConfigManager.get_instance()
        assert isinstance(config.kafka, KafkaConfig)
        assert isinstance(config.kafka.bootstrap_servers, str)

    def test_delta_lake_property(self):
        """Test Delta Lake configuration access."""
        config = ConfigManager.get_instance()
        assert isinstance(config.delta_lake, DeltaLakeConfig)
        assert isinstance(config.delta_lake.base_path, str)

    def test_stage_properties(self):
        """Test stage configuration access."""
        config = ConfigManager.get_instance()
        assert config.stage1.batch_size > 0
        assert config.stage2.max_workers > 0
        assert config.stage3.batch_size > 0
        assert config.stage4.max_workers > 0


class TestConfigManagerDotNotation:
    """Test dot-notation configuration access."""

    def test_get_nested_value(self):
        """Test getting nested configuration values."""
        config = ConfigManager.get_instance()
        redis_host = config.get('redis.host')
        assert isinstance(redis_host, str)

    def test_get_with_default(self):
        """Test getting configuration with default value."""
        config = ConfigManager.get_instance()
        nonexistent = config.get('nonexistent.key', 'default')
        assert nonexistent == 'default'

    def test_get_stage_config(self):
        """Test getting stage-specific configuration."""
        config = ConfigManager.get_instance()
        batch_size = config.get('stage1.batch_size')
        assert isinstance(batch_size, int)
        assert batch_size > 0


class TestEnvironmentVariableOverrides:
    """Test that environment variables override YAML config."""

    def test_redis_host_override(self, monkeypatch):
        """Test Redis host environment variable override."""
        monkeypatch.setenv('REDIS_HOST', 'test-redis-host')
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.redis.host == 'test-redis-host'

    def test_database_password_override(self, monkeypatch):
        """Test database password environment variable override."""
        monkeypatch.setenv('DB_PASSWORD', 'test-password')
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.database.password == 'test-password'

    def test_kafka_servers_override(self, monkeypatch):
        """Test Kafka servers environment variable override."""
        monkeypatch.setenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka1:9092,kafka2:9092')
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.kafka.bootstrap_servers == 'kafka1:9092,kafka2:9092'

    def test_log_level_override(self, monkeypatch):
        """Test log level environment variable override."""
        monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.logging.level == 'DEBUG'


class TestYAMLConfigLoading:
    """Test YAML configuration file loading."""

    def test_load_from_custom_yaml(self, tmp_path):
        """Test loading configuration from custom YAML file."""
        # Create test YAML file
        config_data = {
            'redis': {
                'host': 'custom-redis',
                'port': 6380,
            },
            'database': {
                'host': 'custom-db',
                'port': 5433,
                'database': 'custom_db',
                'user': 'custom_user',
                'password': 'custom_pass',
            },
        }

        config_file = tmp_path / 'test_config.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Load config from custom file
        config = ConfigManager(config_path=config_file, env_override=Environment.TESTING)

        assert config.redis.host == 'custom-redis'
        assert config.redis.port == 6380
        assert config.database.host == 'custom-db'
        assert config.database.port == 5433


class TestConfigExport:
    """Test configuration export functionality."""

    def test_to_dict(self):
        """Test exporting configuration to dictionary."""
        config = ConfigManager.get_instance()
        config_dict = config.to_dict()

        assert isinstance(config_dict, dict)
        assert 'redis' in config_dict
        assert 'database' in config_dict
        assert 'stage1' in config_dict

    def test_to_yaml(self, tmp_path):
        """Test exporting configuration to YAML."""
        config = ConfigManager.get_instance()

        yaml_file = tmp_path / 'exported_config.yml'
        yaml_str = config.to_yaml(file_path=yaml_file)

        # Check YAML string is valid
        assert isinstance(yaml_str, str)
        assert 'redis' in yaml_str

        # Check file was written
        assert yaml_file.exists()

        # Check file can be parsed
        with open(yaml_file) as f:
            loaded = yaml.safe_load(f)
        assert 'redis' in loaded


class TestConfigValidation:
    """Test configuration validation."""

    def test_invalid_environment(self):
        """Test that invalid environment raises error."""
        with pytest.raises(ValueError):
            ConfigManager(env_override='invalid_env')

    def test_redis_fakeredis_detection(self):
        """Test fakeredis URL detection."""
        config = ConfigManager.get_instance()

        # Test with regular Redis URL
        config.redis.url = 'redis://localhost:6379/0'
        assert not config.redis.is_fakeredis

        # Test with fakeredis URL
        config.redis.url = 'fakeredis://'
        assert config.redis.is_fakeredis


class TestBackwardCompatibility:
    """Test backward compatibility with old Config class."""

    def test_config_class_deprecated_warning(self):
        """Test that old Config class shows deprecation warning."""
        from src.common.config_manager import Config

        with pytest.warns(DeprecationWarning):
            Config.get_instance()


# Integration test
class TestConfigManagerIntegration:
    """Integration tests for ConfigManager."""

    def test_full_config_loading_flow(self, monkeypatch):
        """Test complete configuration loading with all sources."""
        # Set environment variables
        monkeypatch.setenv('REDIS_HOST', 'env-redis')
        monkeypatch.setenv('DB_PASSWORD', 'env-password')
        monkeypatch.setenv('LOG_LEVEL', 'DEBUG')

        # Create new config instance
        config = ConfigManager(env_override=Environment.TESTING)

        # Verify environment variables took precedence
        assert config.redis.host == 'env-redis'
        assert config.database.password == 'env-password'
        assert config.logging.level == 'DEBUG'

        # Verify defaults are still present
        assert config.stage1.batch_size > 0
        assert len(config.delta_lake.tables) > 0
