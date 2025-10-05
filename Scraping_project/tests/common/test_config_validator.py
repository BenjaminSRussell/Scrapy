"""
Tests for configuration health check and validation utilities.
"""

from unittest.mock import Mock, patch

import pytest
import yaml

from src.common.config_validator import ConfigHealthCheck, ValidationIssue
from src.orchestrator.config import Config


class TestConfigHealthCheck:
    """Test configuration health check functionality"""

    def create_temp_config(self, tmp_path, config_dict):
        """Helper to create temporary config file"""
        config_file = tmp_path / 'test.yml'
        with open(config_file, 'w') as f:
            yaml.dump(config_dict, f)
        return config_file

    def test_healthy_config_passes(self, tmp_path):
        """Test that a healthy configuration passes all checks"""
        # Create seed file
        seed_file = tmp_path / 'seeds.csv'
        seed_file.write_text('url\nhttps://example.com')

        config_dict = {
            'environment': 'test',
            'scrapy': {
                'concurrent_requests': 32,
                'concurrent_requests_per_domain': 16,
                'concurrent_requests_per_ip': 16,
                'download_delay': 0.5,
            },
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com'],
                    'seed_file': str(seed_file),
                    'output_file': str(tmp_path / 'output.jsonl'),
                    'dedup_cache_path': str(tmp_path / 'cache.db'),
                    'headless_browser': {'enabled': False}
                },
                'validation': {
                    'max_workers': 16,
                    'output_file': str(tmp_path / 'validated.jsonl')
                },
                'enrichment': {
                    'allowed_domains': ['example.com'],
                    'nlp_enabled': False,
                    'output_file': str(tmp_path / 'enriched.jsonl'),
                    'headless_browser': {'enabled': False}
                }
            },
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            },
            'queue': {'max_queue_size': 10000},
            'logging': {'level': 'INFO'},
            'nlp': {},
            'content': {},
            'alerts': {'enabled': False}
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            checker = ConfigHealthCheck(config)
            is_healthy, issues = checker.run_all_checks()

            # Should be healthy with only info messages about created directories
            assert is_healthy is True
            # May have info issues about created directories
            errors = [i for i in issues if i.severity == 'error']
            assert len(errors) == 0
        finally:
            Config.config_dir = original_config_dir

    def test_missing_seed_file_detected(self, tmp_path):
        """Test that missing seed file is detected"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com'],
                    'seed_file': '/nonexistent/seeds.csv',  # Missing file
                    'dedup_cache_path': str(tmp_path / 'cache.db')
                },
                 'enrichment': {
                    'allowed_domains': ['example.com']
                }
            },
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            }
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            checker = ConfigHealthCheck(config)
            is_healthy, issues = checker.run_all_checks()

            # Should detect missing seed file
            assert is_healthy is False
            errors = [i for i in issues if i.severity == 'error' and 'seed' in i.message.lower()]
            assert len(errors) >= 1
            assert 'not found' in errors[0].message.lower() or 'seed' in errors[0].message.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_missing_nlp_model_detected(self, tmp_path):
        """Test that missing NLP model is detected"""
        config_dict = {
            'environment': 'test',
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com']
                },
                'enrichment': {
                    'allowed_domains': ['example.com'],
                    'nlp_enabled': True,
                }
            },
            'nlp': {
                'spacy_model': 'en_core_web_sm'
            },
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            }
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            with patch('spacy.load', side_effect=OSError("Model not found")):
                checker = ConfigHealthCheck(config)
                is_healthy, issues = checker.run_all_checks()

                # Should detect missing NLP model
                assert is_healthy is False
                errors = [i for i in issues if i.severity == 'error' and 'spacy' in i.message.lower()]
                assert len(errors) >= 1
                assert 'model' in errors[0].message.lower()
        finally:
            Config.config_dir = original_config_dir

    def test_high_concurrency_warning(self, tmp_path):
        """Test that high concurrency values generate warnings"""
        config_dict = {
            'environment': 'test',
            'scrapy': {
                'concurrent_requests': 200,  # Very high
            },
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com']
                },
                'validation': {
                    'max_workers': 100,  # Very high
                },
                'enrichment': {
                    'allowed_domains': ['example.com']
                }
            },
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            }
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            checker = ConfigHealthCheck(config)
            is_healthy, issues = checker.run_all_checks()

            # Should generate warnings but not errors
            warnings = [i for i in issues if i.severity == 'warning']
            assert len(warnings) >= 1
            # Check for concurrency warnings
            concurrency_warnings = [w for w in warnings if 'concurrent' in w.message.lower() or 'workers' in w.message.lower()]
            assert len(concurrency_warnings) >= 1
        finally:
            Config.config_dir = original_config_dir

    def test_zero_download_delay_warning(self, tmp_path):
        """Test that zero download delay generates a warning"""
        config_dict = {
            'environment': 'test',
            'scrapy': {
                'download_delay': 0,  # No rate limiting
            },
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com']
                },
                'enrichment': {
                    'allowed_domains': ['example.com']
                }
            },
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            }
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            checker = ConfigHealthCheck(config)
            is_healthy, issues = checker.run_all_checks()

            # Should generate warning about download_delay
            warnings = [i for i in issues if i.severity == 'warning' and 'download_delay' in i.message.lower()]
            assert len(warnings) >= 1
        finally:
            Config.config_dir = original_config_dir

    def test_validation_issue_dataclass(self):
        """Test ValidationIssue dataclass"""
        issue = ValidationIssue(
            severity='error',
            category='filesystem',
            message='File not found',
            suggestion='Create the file'
        )

        assert issue.severity == 'error'
        assert issue.category == 'filesystem'
        assert issue.message == 'File not found'
        assert issue.suggestion == 'Create the file'

    def test_print_report_no_issues(self, capsys, tmp_path):
        """Test print_report with no issues"""
        config_dict = {
            'environment': 'test',
            'data': {
                'raw_dir': str(tmp_path / 'raw'),
                'processed_dir': str(tmp_path / 'processed'),
                'cache_dir': str(tmp_path / 'cache'),
                'logs_dir': str(tmp_path / 'logs'),
            },
            'stages': {
                'discovery': {
                    'allowed_domains': ['example.com'],
                    'headless_browser': {'enabled': False}
                },
                'enrichment': {
                    'allowed_domains': ['example.com'],
                    'nlp_enabled': False,
                    'headless_browser': {'enabled': False}
                }
            }
        }

        self.create_temp_config(tmp_path, config_dict)

        original_config_dir = Config.config_dir
        try:
            Config.config_dir = tmp_path
            config = Config(env='test', validate=True)

            checker = ConfigHealthCheck(config)
            checker.issues = []  # Clear any issues
            checker.print_report()

            captured = capsys.readouterr()
            assert 'PASSED' in captured.out or 'No issues' in captured.out
        finally:
            Config.config_dir = original_config_dir

    def test_print_report_with_errors(self, capsys):
        """Test print_report with errors"""
        checker = ConfigHealthCheck(Mock())
        checker.issues = [
            ValidationIssue('error', 'filesystem', 'File not found', 'Create it'),
            ValidationIssue('warning', 'logic', 'High concurrency', 'Lower it'),
        ]

        checker.print_report()

        captured = capsys.readouterr()
        assert 'ERRORS' in captured.out
        assert 'WARNINGS' in captured.out
        assert 'File not found' in captured.out
        assert 'High concurrency' in captured.out
        assert 'FAILED' in captured.out


if __name__ == '__main__':
    pytest.main([__file__, '-v'])