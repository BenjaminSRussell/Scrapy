"""Tests for src/core/config.py's Config class.

Replaces the old ConfigManager test suite, which tested a richer
dataclass-based API (typed AppConfig/DatabaseConfig/RedisConfig/
KafkaConfig sub-objects, per-field env var overrides via
Environment.TESTING, YAML export) that was deleted with no
replacement of equivalent shape when src/common/config_manager.py was
consolidated into src/core/config.py - see that module's docstring:
"Consolidated from src/common/config.py, src/common/config_manager.py".
The current Config is a flat dict wrapper with dot-notation get/set;
these tests cover its actual surface rather than the deleted one.
"""

import pytest
import yaml

from src.core.config import Config, get_config, reset_config

class TestConfigSingleton:

    def teardown_method(self):
        Config.reset_instance()

    def test_get_instance_returns_same_object(self):
        config1 = Config.get_instance()
        config2 = Config.get_instance()
        assert config1 is config2

    def test_reset_instance_creates_new_object(self):
        config1 = Config.get_instance()
        Config.reset_instance()
        config2 = Config.get_instance()
        assert config1 is not config2

class TestGlobalConfigSingleton:

    def teardown_method(self):
        reset_config()

    def test_get_config_returns_same_object(self):
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_reset_config_creates_new_object(self):
        config1 = get_config()
        reset_config()
        config2 = get_config()
        assert config1 is not config2

class TestConfigDotNotation:

    def test_get_nested_value(self):
        config = Config()
        assert isinstance(config.get("redis.host"), str)

    def test_get_missing_key_returns_default(self):
        config = Config()
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_get_missing_key_without_default_returns_none(self):
        config = Config()
        assert config.get("nonexistent.key") is None

    def test_set_creates_nested_path(self):
        config = Config()
        config.set("stage1.custom_setting", 42)
        assert config.get("stage1.custom_setting") == 42

    def test_set_overwrites_existing_value(self):
        config = Config()
        config.set("redis.host", "custom-redis")
        assert config.get("redis.host") == "custom-redis"

class TestConfigSections:

    def test_get_section_returns_dict(self):
        config = Config()
        redis_section = config.get_section("redis")
        assert isinstance(redis_section, dict)
        assert "host" in redis_section
        assert "port" in redis_section

    def test_get_missing_section_returns_empty_dict(self):
        config = Config()
        assert config.get_section("nonexistent") == {}

class TestConfigDefaults:

    def test_redis_host_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("REDIS_HOST", "env-redis-host")
        config = Config(config_path=tmp_path / "nonexistent.yml")
        assert config.get("redis.host") == "env-redis-host"

    def test_stage_sections_present(self):
        # config.yml (loaded by default) keeps stage1-4 as top-level
        # sections, not nested under "stages" (that nesting only exists
        # in Config._default_config()'s fallback, used when no config.yml
        # is found).
        config = Config()
        for stage in ("stage1", "stage2", "stage3", "stage4"):
            assert config.get_section(stage)

class TestConfigYamlLoading:

    def test_load_from_custom_yaml(self, tmp_path):
        config_data = {
            "redis": {"host": "custom-redis", "port": 6380},
        }
        config_file = tmp_path / "test_config.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config(config_path=config_file)

        assert config.get("redis.host") == "custom-redis"
        assert config.get("redis.port") == 6380

    def test_missing_file_falls_back_to_defaults(self, tmp_path):
        config = Config(config_path=tmp_path / "does_not_exist.yml")
        assert config.get("redis.host") is not None

    def test_reload_picks_up_file_changes(self, tmp_path):
        config_file = tmp_path / "test_config.yml"
        with open(config_file, "w") as f:
            yaml.dump({"redis": {"host": "first"}}, f)

        config = Config(config_path=config_file)
        assert config.get("redis.host") == "first"

        with open(config_file, "w") as f:
            yaml.dump({"redis": {"host": "second"}}, f)

        config.reload()
        assert config.get("redis.host") == "second"

class TestConfigRawExport:

    def test_get_raw_config_returns_dict(self):
        config = Config()
        raw = config.get_raw_config()
        assert isinstance(raw, dict)
        assert raw["redis"]["host"] == config.get("redis.host")

        # get_raw_config() is a shallow copy: reassigning a top-level key
        # doesn't affect the live config, but mutating a nested dict does.
        raw["new_top_level_key"] = "added"
        assert config.get("new_top_level_key") is None
