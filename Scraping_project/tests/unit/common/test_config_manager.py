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

    def test_singleton_pattern(self):
        config1 = ConfigManager.get_instance()
        config2 = ConfigManager.get_instance()
        assert config1 is config2

    def test_reset_singleton(self):
        config1 = ConfigManager.get_instance()
        config2 = ConfigManager.get_instance(reset=True)
        assert config1 is not config2

    def test_config_property(self):
        config_manager = ConfigManager.get_instance()
        assert isinstance(config_manager.config, AppConfig)

class TestConfigManagerProperties:

    def test_database_property(self):
        config = ConfigManager.get_instance()
        assert isinstance(config.database, DatabaseConfig)
        assert isinstance(config.database.host, str)
        assert isinstance(config.database.port, int)

    def test_redis_property(self):
        config = ConfigManager.get_instance()
        assert isinstance(config.redis, RedisConfig)
        assert isinstance(config.redis.host, str)
        assert isinstance(config.redis.port, int)

    def test_kafka_property(self):
        config = ConfigManager.get_instance()
        assert isinstance(config.kafka, KafkaConfig)
        assert isinstance(config.kafka.bootstrap_servers, str)

    def test_delta_lake_property(self):
        config = ConfigManager.get_instance()
        assert isinstance(config.delta_lake, DeltaLakeConfig)
        assert isinstance(config.delta_lake.base_path, str)

    def test_stage_properties(self):
        config = ConfigManager.get_instance()
        assert config.stage1.batch_size > 0
        assert config.stage2.max_workers > 0
        assert config.stage3.batch_size > 0
        assert config.stage4.max_workers > 0

class TestConfigManagerDotNotation:

    def test_get_nested_value(self):
        config = ConfigManager.get_instance()
        redis_host = config.get("redis.host")
        assert isinstance(redis_host, str)

    def test_get_with_default(self):
        config = ConfigManager.get_instance()
        nonexistent = config.get("nonexistent.key", "default")
        assert nonexistent == "default"

    def test_get_stage_config(self):
        config = ConfigManager.get_instance()
        batch_size = config.get("stage1.batch_size")
        assert isinstance(batch_size, int)
        assert batch_size > 0

class TestEnvironmentVariableOverrides:

    def test_redis_host_override(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "test-redis-host")
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.redis.host == "test-redis-host"

    def test_database_password_override(self, monkeypatch):
        monkeypatch.setenv("DB_PASSWORD", "test-password")
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.database.password == "test-password"

    def test_kafka_servers_override(self, monkeypatch):
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka1:9092,kafka2:9092")
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.kafka.bootstrap_servers == "kafka1:9092,kafka2:9092"

    def test_log_level_override(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        config = ConfigManager(env_override=Environment.TESTING)
        assert config.logging.level == "DEBUG"

class TestYAMLConfigLoading:

    def test_load_from_custom_yaml(self, tmp_path):
        config_data = {
            "redis": {
                "host": "custom-redis",
                "port": 6380,
            },
            "database": {
                "host": "custom-db",
                "port": 5433,
                "database": "custom_db",
                "user": "custom_user",
                "password": "custom_pass",
            },
        }

        config_file = tmp_path / "test_config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = ConfigManager(config_path=config_file, env_override=Environment.TESTING)

        assert config.redis.host == "custom-redis"
        assert config.redis.port == 6380
        assert config.database.host == "custom-db"
        assert config.database.port == 5433

class TestConfigExport:

    def test_to_dict(self):
        config = ConfigManager.get_instance()
        config_dict = config.to_dict()

        assert isinstance(config_dict, dict)
        assert "redis" in config_dict
        assert "database" in config_dict
        assert "stage1" in config_dict

    def test_to_yaml(self, tmp_path):
        config = ConfigManager.get_instance()

        yaml_file = tmp_path / "exported_config.yml"
        yaml_str = config.to_yaml(file_path=yaml_file)

        assert isinstance(yaml_str, str)
        assert "redis" in yaml_str

        assert yaml_file.exists()

        with open(yaml_file) as f:
            loaded = yaml.safe_load(f)
        assert "redis" in loaded

class TestConfigValidation:

    def test_invalid_environment(self):
        with pytest.raises(ValueError):
            ConfigManager(env_override="invalid_env")

    def test_redis_fakeredis_detection(self):
        config = ConfigManager.get_instance()

        config.redis.url = "redis://localhost:6379/0"
        assert not config.redis.is_fakeredis

        config.redis.url = "fakeredis://"
        assert config.redis.is_fakeredis

class TestBackwardCompatibility:

    def test_config_class_deprecated_warning(self):
        from src.common.config_manager import Config

        with pytest.warns(DeprecationWarning):
            Config.get_instance()

class TestConfigManagerIntegration:

    def test_full_config_loading_flow(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "env-redis")
        monkeypatch.setenv("DB_PASSWORD", "env-password")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        config = ConfigManager(env_override=Environment.TESTING)

        assert config.redis.host == "env-redis"
        assert config.database.password == "env-password"
        assert config.logging.level == "DEBUG"

        assert config.stage1.batch_size > 0
        assert len(config.delta_lake.tables) > 0
