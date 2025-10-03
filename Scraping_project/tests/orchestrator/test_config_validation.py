"""
Tests for comprehensive Pydantic-based configuration validation.
Demonstrates catching type errors, typos, and range violations.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.orchestrator.config import Config, ConfigValidationError


class TestConfigValidation:
    """Test configuration validation catches various error types"""

    def create_temp_config(self, config_dict):
        """Helper to create temporary config file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(config_dict, f)
            return Path(f.name)

    def test_valid_config_passes(self, tmp_path):
        """Test that valid configuration passes validation"""
        config_dict = {
            'environment': 'test',
            'scrapy': {
                'concurrent_requests': 32,
                'concurrent_requests_per_domain': 16,
                'concurrent_requests_per_ip': 16,
                'download_delay': 0.1,
                'download_timeout': 10,
                'dns_timeout': 5,
                'retry_enabled': True,
                'retry_times': 2,
                'robotstxt_obey': False,
                'user_agent': 'TestBot/1.0',
                'log_level': 'INFO',
                'request_fingerprinter_implementation': '2.7'
            },
            'stages': {
                'discovery': {
                    'spider_name': 'discovery',
                    'allowed_domains': ['example.com'],
                    'max_depth': 3,
                    'output_file': 'data/processed/stage01/discovery_output.jsonl',
                    'seed_file': 'data/raw/seeds.csv',
                    'use_persistent_dedup': True,
                    'dedup_cache_path': 'data/cache/url_cache.db',
                    'headless_browser': {
                        'enabled': False,
                        'engine': 'playwright',
                        'browser_type': 'chromium',
                        'headless': True,
                        'timeout': 30000,
                        'wait_for_selector': None,
                        'screenshot_on_error': False,
                        'user_agent': 'Mozilla/5.0',
                        'viewport': {'width': 1920, 'height': 1080}
                    },
                    'heuristics': {
                        'dynamic_script_hints': ['fetch(', 'axios'],
                        'json_url_key_hints': ['url', 'href'],
                        'data_attribute_candidates': ['data-url', 'data-src']
                    }
                },
                'validation': {
                    'max_workers': 16,
                    'timeout': 15,
                    'output_file': 'data/processed/stage02/validated_urls.jsonl'
                },
                'enrichment': {
                    'spider_name': 'enrichment',
                    'allowed_domains': ['example.com'],
                    'nlp_enabled': True,
                    'max_text_length': 20000,
                    'top_keywords': 15,
                    'batch_size': 1000,
                    'output_file': 'data/processed/stage03/enrichment_output.jsonl',
                    'headless_browser': {
                        'enabled': False,
                        'engine': 'playwright',
                        'browser_type': 'chromium',
                        'headless': True,
                        'timeout': 30000,
                        'wait_for_selector': None,
                        'screenshot_on_error': False,
                        'viewport': {'width': 1920, 'height': 1080}
                    },
                    'content_types': {
                        'enabled_types': ['text/html', 'application/pdf'],
                        'pdf': {
                            'extract_text': True,
                            'extract_metadata': True,
                            'max_pages': 100
                        },
                        'media': {
                            'extract_metadata': True,
                            'download_thumbnails': False,
                            'thumbnail_dir': 'data/processed/media/thumbnails'
                        }
                    }
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
            },
            'queue': {
                'max_queue_size': 10000,
                'batch_size': 1000,
                'backpressure_warning_threshold': 0.8,
                'backpressure_critical_threshold': 0.95
            },
            'logging': {
                'level': 'INFO',
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                'file': 'data/logs/pipeline.log',
                'max_bytes': 10485760,
                'backup_count': 3,
                'structured': False
            },
            'nlp': {
                'model': 'en_core_web_sm',
                'max_text_length': 20000,
                'top_keywords': 15
            },
            'content': {
                'predefined_tags': ['admissions', 'research']
            },
            'alerts': {
                'enabled': False,
                'severity_threshold': 'error',
                'alert_file': 'data/logs/alerts.jsonl',
                'channels': []
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        # Temporarily patch Config.config_dir to use tmp_path
        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)
            assert config is not None
        finally:
            Config.config_dir = original_config_dir

    def test_type_error_string_instead_of_int(self, tmp_path):
        """Test that type errors are caught (e.g., string "5" instead of int 5)"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'max_depth': "5"  # String instead of int - should PASS with coercion
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            # Pydantic will coerce "5" to 5, so this should pass
            config = Config(env='test', validate=True)
            assert config.get('stages', 'discovery', 'max_depth') == 5
        finally:
            Config.config_dir = original_config_dir

    def test_type_error_invalid_string(self, tmp_path):
        """Test that invalid strings for numeric fields are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'max_depth': "not_a_number"  # Invalid - cannot coerce
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            assert 'Type error' in str(exc_info.value) or 'validation error' in str(exc_info.value).lower()
        finally:
            Config.config_dir = original_config_dir

    def test_unknown_key_typo_maxDepth(self, tmp_path):
        """Test that typos in config keys are caught (maxDepth instead of max_depth)"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'maxDepth': 5,  # Typo: should be max_depth
                    'allowed_domains': ['example.com']
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'maxDepth' in error_msg or 'Unknown' in error_msg or 'extra' in error_msg.lower()
            assert 'extra inputs are not permitted' in error_msg.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_unknown_key_in_nested_section(self, tmp_path):
        """Test that typos in nested sections are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'headless_browser': {
                        'enabled': True,
                        'engien': 'playwright',  # Typo: should be 'engine'
                    }
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'engien' in error_msg or 'Unknown' in error_msg or 'extra' in error_msg.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_value_out_of_range(self, tmp_path):
        """Test that values outside allowed ranges are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'max_depth': 15  # Out of range: must be <= 10
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'less than or equal to 10' in error_msg or 'max_depth' in error_msg
        finally:
            Config.config_dir = original_config_dir

    def test_negative_value_caught(self, tmp_path):
        """Test that negative values for positive-only fields are caught"""
        config_dict = {
            'environment': 'test',
            'scrapy': {
                'concurrent_requests': -5  # Invalid: must be >= 1
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'greater than or equal to 1' in error_msg or 'concurrent_requests' in error_msg
        finally:
            Config.config_dir = original_config_dir

    def test_invalid_enum_value(self, tmp_path):
        """Test that invalid enum values are caught"""
        config_dict = {
            'environment': 'test',
            'logging': {
                'level': 'TRACE'  # Invalid: must be DEBUG, INFO, WARNING, ERROR, or CRITICAL
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'TRACE' in error_msg or 'level' in error_msg
        finally:
            Config.config_dir = original_config_dir

    def test_invalid_domain_format(self, tmp_path):
        """Test that invalid domain formats are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'allowed_domains': ['invalid_domain']  # Invalid format
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'domain' in error_msg.lower() or 'invalid_domain' in error_msg
        finally:
            Config.config_dir = original_config_dir

    def test_threshold_validation_warning_greater_than_critical(self, tmp_path):
        """Test that logical constraints are validated (warning < critical)"""
        config_dict = {
            'environment': 'test',
            'queue': {
                'backpressure_warning_threshold': 0.95,
                'backpressure_critical_threshold': 0.80  # Invalid: warning must be < critical
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'warning' in error_msg.lower() and 'critical' in error_msg.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_concurrency_hierarchy_validation(self, tmp_path):
        """Test that per-domain concurrency doesn't exceed total"""
        config_dict = {
            'environment': 'test',
            'scrapy': {
                'concurrent_requests': 10,
                'concurrent_requests_per_domain': 20  # Invalid: exceeds total
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'concurrent_requests' in error_msg
        finally:
            Config.config_dir = original_config_dir

    def test_incompatible_browser_engine(self, tmp_path):
        """Test that incompatible browser/engine combinations are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'enrichment': {
                    'headless_browser': {
                        'enabled': True,
                        'engine': 'selenium',
                        'browser_type': 'webkit'  # Invalid: Selenium doesn't support WebKit
                    }
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'webkit' in error_msg.lower() or 'selenium' in error_msg.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_invalid_mime_type_format(self, tmp_path):
        """Test that invalid MIME type formats are caught"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'enrichment': {
                    'content_types': {
                        'enabled_types': ['not-a-valid-mime-type']  # Invalid format
                    }
                }
            }
        }

        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            with pytest.raises(ConfigValidationError) as exc_info:
                Config(env='test', validate=True)

            error_msg = str(exc_info.value)
            assert 'mime' in error_msg.lower() or 'not-a-valid-mime-type' in error_msg
        finally:
            Config.config_dir = original_config_dir


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
