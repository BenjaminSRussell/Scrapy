"""
Tests for comprehensive Pydantic-based configuration validation.
Demonstrates catching type errors, typos, and range violations.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.orchestrator.config import Config, ConfigValidationError


def get_minimal_valid_config():
    """Returns a dictionary representing a minimal, valid configuration."""
    return {
        'environment': 'test',
        'stages': {
            'discovery': {
                'allowed_domains': ['example.com'],
                'seed_file': 'data/raw/seeds.csv'
            },
            'validation': {},
            'enrichment': {
                'allowed_domains': ['example.com']
            }
        },
        'data': {
            'raw_dir': 'data/raw',
            'processed_dir': 'data/processed',
            'catalog_dir': 'data/catalog',
            'cache_dir': 'data/cache',
            'exports_dir': 'data/exports',
            'logs_dir': 'data/logs',
            'temp_dir': 'data/temp'
        }
    }


class TestConfigValidation:
    """Test configuration validation catches various error types"""

    def create_temp_config_file(self, config_dict, tmp_path: Path) -> Path:
        """Helper to create a temporary config file and its required directory structure."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)

        # Create a dummy seed file because the validator checks for its existence.
        # The path in the config is relative to the project root, so we build the temp path accordingly.
        seed_file_path = tmp_path / config_dict['stages']['discovery']['seed_file']
        seed_file_path.parent.mkdir(parents=True, exist_ok=True)
        seed_file_path.touch()

        config_file = config_dir / "test.yml"
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)
        return config_dir

    def test_valid_config_passes(self, tmp_path):
        """Test that a comprehensive, valid configuration passes validation."""
        config_dict = get_minimal_valid_config()
        config_dict['scrapy'] = {'concurrent_requests': 16}
        config_dict['logging'] = {'level': 'INFO'}
        config_dict['stages']['discovery']['max_depth'] = 2
        config_dict['stages']['validation']['max_workers'] = 8

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            config = Config(env='test', validate=True)
            assert config is not None
            assert config.get('scrapy', 'concurrent_requests') == 16

    def test_type_error_string_instead_of_int(self, tmp_path):
        """Test that Pydantic correctly coerces string numbers to integers."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['max_depth'] = "5"

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            config = Config(env='test', validate=True)
            assert config.get('stages', 'discovery', 'max_depth') == 5

    def test_type_error_invalid_string(self, tmp_path):
        """Test that invalid strings for numeric fields are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['max_depth'] = "not_a_number"

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'validation error' in str(exc_info.value).lower()

    def test_unknown_key_typo_maxDepth(self, tmp_path):
        """Test that typos in config keys are caught (e.g., maxDepth)."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['maxDepth'] = 5

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            error_msg = str(exc_info.value).lower()
            assert 'maxdepth' in error_msg and 'extra inputs are not permitted' in error_msg

    def test_unknown_key_in_nested_section(self, tmp_path):
        """Test that typos in nested sections are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['headless_browser'] = {'engien': 'playwright'}

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'engien' in str(exc_info.value)
            assert 'extra inputs are not permitted' in str(exc_info.value).lower()

    def test_value_out_of_range(self, tmp_path):
        """Test that values outside allowed ranges are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['max_depth'] = 15

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'less than or equal to 10' in str(exc_info.value)

    def test_negative_value_caught(self, tmp_path):
        """Test that negative values for positive-only fields are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['scrapy'] = {'concurrent_requests': -5}

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'greater than or equal to 1' in str(exc_info.value)

    def test_invalid_enum_value(self, tmp_path):
        """Test that invalid enum values are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['logging'] = {'level': 'TRACE'}

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            error_msg = str(exc_info.value).lower()
            assert 'trace' in error_msg
            assert 'input should be' in error_msg

    def test_invalid_domain_format(self, tmp_path):
        """Test that invalid domain formats are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['discovery']['allowed_domains'] = ['example-.com']

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'invalid domain format' in str(exc_info.value).lower()

    def test_threshold_validation_warning_greater_than_critical(self, tmp_path):
        """Test logical validation: warning threshold must be < critical threshold."""
        config_dict = get_minimal_valid_config()
        config_dict['queue'] = {
            'backpressure_warning_threshold': 0.95,
            'backpressure_critical_threshold': 0.80
        }

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            error_msg = str(exc_info.value).lower()
            assert 'warning' in error_msg and 'critical' in error_msg

    def test_concurrency_hierarchy_validation(self, tmp_path):
        """Test logical validation: per-domain concurrency cannot exceed total."""
        config_dict = get_minimal_valid_config()
        config_dict['scrapy'] = {
            'concurrent_requests': 10,
            'concurrent_requests_per_domain': 20
        }

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'cannot exceed concurrent_requests' in str(exc_info.value)

    def test_incompatible_browser_engine(self, tmp_path):
        """Test logical validation: browser engine and type must be compatible."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['enrichment']['headless_browser'] = {
            'enabled': True,
            'engine': 'selenium',
            'browser_type': 'webkit'
        }

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            error_msg = str(exc_info.value).lower()
            assert 'webkit' in error_msg and 'selenium' in error_msg

    def test_invalid_mime_type_format(self, tmp_path):
        """Test that invalid MIME type formats are caught."""
        config_dict = get_minimal_valid_config()
        config_dict['stages']['enrichment']['content_types'] = {
            'enabled_types': ['not-a-valid-mime-type']
        }

        config_dir = self.create_temp_config_file(config_dict, tmp_path)

        with patch.object(Config, 'config_dir', config_dir):
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)
            assert 'invalid mime type format' in str(exc_info.value).lower()