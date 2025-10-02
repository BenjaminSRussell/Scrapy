"""Tests for configuration management."""

from __future__ import annotations

import os
import sys
import tempfile
import yaml
from pathlib import Path
from unittest.mock import patch

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from orchestrator.config import Config


@pytest.fixture
def sample_config_data():
    """Sample configuration data for testing."""
    return {
        'scrapy': {
            'concurrent_requests': 32,
            'download_delay': 0.1,
            'user_agent': 'TestSpider/1.0'
        },
        'stages': {
            'discovery': {
                'max_depth': 3,
                'batch_size': 100,
                'output_file': 'data/processed/stage01/discovery_output.jsonl'
            },
            'validation': {
                'max_workers': 10,
                'timeout': 5,
                'output_file': 'data/processed/stage02/validated_urls.jsonl'
            },
            'enrichment': {
                'nlp_enabled': True,
                'max_text_length': 2000,
                'top_keywords': 10,
                'output_file': 'data/processed/stage03/enriched_content.jsonl'
            }
        },
        'logging': {
            'level': 'INFO',
            'file': 'logs/pipeline.log'
        },
        'data': {
            'raw_dir': 'data/raw',
            'processed_dir': 'data/processed',
            'catalog_dir': 'data/catalog',
            'cache_dir': 'data/cache',
            'exports_dir': 'data/exports',
            'logs_dir': 'logs'
        }
    }


@pytest.fixture
def temp_config_file(sample_config_data, tmp_path):
    """Create temporary config file for testing."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "test.yml"

    with open(config_file, 'w') as f:
        yaml.dump(sample_config_data, f)

    return config_dir, config_file


def test_config_loads_yaml_file(temp_config_file, sample_config_data):
    """Test Config loads YAML configuration file correctly."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    assert config.get('scrapy', 'concurrent_requests') == 32
    assert config.get('scrapy', 'user_agent') == 'TestSpider/1.0'
    assert config.get('stages', 'discovery', 'max_depth') == 3


def test_config_missing_file_raises_error(tmp_path):
    """Test Config raises error when config file doesn't exist."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    with patch.object(Config, 'config_dir', config_dir):
        with pytest.raises(FileNotFoundError):
            Config(env='nonexistent')


def test_config_get_method_with_defaults(temp_config_file):
    """Test Config.get() method with default values."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    # Existing value
    assert config.get('scrapy', 'concurrent_requests') == 32

    # Non-existing value with default
    assert config.get('nonexistent', 'key', default='default_value') == 'default_value'

    # Non-existing value without default
    assert config.get('nonexistent', 'key') is None


def test_config_environment_variable_overrides(temp_config_file):
    """Test Config applies environment variable overrides."""
    config_dir, config_file = temp_config_file

    with patch.dict(os.environ, {
        'SCRAPY_CONCURRENT_REQUESTS': '64',
        'SCRAPY_DOWNLOAD_DELAY': '0.5',
        'STAGE1_MAX_DEPTH': '5'
    }):
        with patch.object(Config, 'config_dir', config_dir):
            config = Config(env='test')

    assert config.get('scrapy', 'concurrent_requests') == 64
    assert config.get('scrapy', 'download_delay') == 0.5
    assert config.get('stages', 'discovery', 'max_depth') == 5


def test_config_get_scrapy_settings(temp_config_file):
    """Test Config.get_scrapy_settings() returns proper Scrapy configuration."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    scrapy_settings = config.get_scrapy_settings()

    assert scrapy_settings['CONCURRENT_REQUESTS'] == 32
    assert scrapy_settings['DOWNLOAD_DELAY'] == 0.1
    assert scrapy_settings['USER_AGENT'] == 'TestSpider/1.0'
    assert scrapy_settings['HTTPERROR_ALLOW_ALL'] is True


def test_config_get_stage_configs(temp_config_file):
    """Test Config stage-specific configuration methods."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    # Stage 1 config
    stage1_config = config.get_stage1_config()
    assert stage1_config['max_depth'] == 3
    assert stage1_config['batch_size'] == 100
    assert stage1_config['output_file'] == 'data/processed/stage01/discovery_output.jsonl'

    # Stage 2 config
    stage2_config = config.get_stage2_config()
    assert stage2_config['max_workers'] == 10
    assert stage2_config['timeout'] == 5
    assert stage2_config['output_file'] == 'data/processed/stage02/validated_urls.jsonl'

    # Stage 3 config
    stage3_config = config.get_stage3_config()
    assert stage3_config['nlp_enabled'] is True
    assert stage3_config['max_text_length'] == 2000
    assert stage3_config['top_keywords'] == 10
    assert stage3_config['output_file'] == 'data/processed/stage03/enriched_content.jsonl'


def test_config_get_logging_config(temp_config_file):
    """Test Config.get_logging_config() returns logging configuration."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    logging_config = config.get_logging_config()
    assert logging_config['level'] == 'INFO'
    assert logging_config['file'] == 'logs/pipeline.log'
    assert logging_config['max_bytes'] == 10485760  # Default
    assert logging_config['backup_count'] == 3      # Default


def test_config_get_data_paths(temp_config_file):
    """Test Config.get_data_paths() returns Path objects."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    data_paths = config.get_data_paths()

    assert isinstance(data_paths['raw_dir'], Path)
    assert str(data_paths['raw_dir']) == 'data/raw'
    assert isinstance(data_paths['processed_dir'], Path)
    assert str(data_paths['processed_dir']) == 'data/processed'
    assert isinstance(data_paths['logs_dir'], Path)
    assert str(data_paths['logs_dir']) == 'logs'


def test_config_environment_property(temp_config_file):
    """Test Config.environment property returns current environment."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    assert config.environment == 'test'


@pytest.mark.parametrize("env_value, expected_value", [
    ("100", 100),
])
def test_config_env_override_type_conversion_valid(temp_config_file, env_value, expected_value):
    """Test valid environment variable type conversion."""
    config_dir, config_file = temp_config_file

    with patch.dict(os.environ, {'SCRAPY_CONCURRENT_REQUESTS': env_value}):
        with patch.object(Config, 'config_dir', config_dir):
            config = Config(env='test')

    value = config.get('scrapy', 'concurrent_requests')
    assert isinstance(value, int)
    assert value == expected_value

@pytest.mark.parametrize("env_value", [
    "1.5",
    "string_value",
    "true",
])
def test_config_env_override_type_conversion_invalid(temp_config_file, env_value):
    """Test that invalid environment variable type conversion raises an error."""
    config_dir, config_file = temp_config_file

    with patch.dict(os.environ, {'SCRAPY_CONCURRENT_REQUESTS': env_value}):
        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError):
                Config(env='test')


def test_config_nested_key_navigation(temp_config_file):
    """Test Config can navigate deeply nested configuration keys."""
    config_dir, config_file = temp_config_file

    with patch.object(Config, 'config_dir', config_dir):
        config = Config(env='test')

    # Deep navigation
    assert config.get('stages', 'discovery', 'max_depth') == 3
    assert config.get('stages', 'enrichment', 'nlp_enabled') is True

    # Non-existent nested path
    assert config.get('stages', 'nonexistent', 'key') is None
    assert config.get('stages', 'discovery', 'nonexistent') is None
