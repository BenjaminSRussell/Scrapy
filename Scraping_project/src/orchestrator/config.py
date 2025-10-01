import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple

from src.common import config_keys as keys

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails"""
    pass


class Config:
    """Configuration manager that reads env + stage YAML files because we need more abstraction"""
    config_dir = Path(__file__).parent.parent.parent / 'config'

    def __init__(self, env: str = 'development', validate: bool = True):
        self.env = env
        self.config_dir = self.config_dir
        self._config = self._load_config()

        if validate:
            self._validate_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file because JSON wasn't good enough"""
        config_file = self.config_dir / f'{self.env}.yml'

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        config = self._apply_env_overrides(config)

        return config

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides because configuration wasn't complex enough"""
        env_overrides = {
            'SCRAPY_CONCURRENT_REQUESTS': [keys.SCRAPY, keys.SCRAPY_CONCURRENT_REQUESTS],
            'SCRAPY_DOWNLOAD_DELAY': [keys.SCRAPY, keys.SCRAPY_DOWNLOAD_DELAY],
            'STAGE1_MAX_DEPTH': [keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH],
        }

        for env_var, config_path in env_overrides.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                try:
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    pass

                current = config
                for key in config_path[:-1]:
                    current = current.setdefault(key, {})
                current[config_path[-1]] = value

        return config

    def _validate_config(self) -> None:
        """Validate configuration schema and required fields"""
        errors = []

        required_fields = [
            ([keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH], int),
            ([keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_OUTPUT_FILE], str),
            ([keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_MAX_WORKERS], int),
            ([keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_TIMEOUT], (int, float)),
            ([keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_OUTPUT_FILE], str),
            ([keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_OUTPUT_FILE], str),
            ([keys.DATA, keys.RAW_DIR], str),
            ([keys.DATA, keys.PROCESSED_DIR], str),
            ([keys.DATA, keys.LOGS_DIR], str),
            ([keys.LOGGING, keys.LOGGING_LEVEL], str),
        ]

        for field_path, expected_type in required_fields:
            value = self.get(*field_path)
            if value is None:
                errors.append(f"Missing required field: {'.'.join(field_path)}")
            elif not isinstance(value, expected_type):
                errors.append(
                    f"Invalid type for {'.'.join(field_path)}: "
                    f"expected {expected_type}, got {type(value).__name__}"
                )

        validations = [
            (([keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH], lambda v: 0 < v <= 10),
             "max_depth must be between 1 and 10"),
            (([keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_MAX_WORKERS], lambda v: 1 <= v <= 100),
             "max_workers must be between 1 and 100"),
            (([keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_TIMEOUT], lambda v: v > 0),
             "timeout must be positive"),
            (([keys.LOGGING, keys.LOGGING_LEVEL], lambda v: v in ['DEBUG', 'INFO', 'WARNING', 'ERROR']),
             "log_level must be DEBUG, INFO, WARNING, or ERROR"),
        ]

        for (field_path, validator), error_msg in validations:
            value = self.get(*field_path)
            if value is not None:
                try:
                    if not validator(value):
                        errors.append(f"{'.'.join(field_path)}: {error_msg}")
                except Exception as e:
                    errors.append(f"Validation error for {'.'.join(field_path)}: {e}")

        if errors:
            error_message = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(error_message)
            raise ConfigValidationError(error_message)

        logger.info("Configuration validation passed")

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get configuration value using dot notation"""
        current = self._config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def get_scrapy_settings(self) -> Dict[str, Any]:
        """Get Scrapy-specific settings"""
        return {
            'CONCURRENT_REQUESTS': self.get(keys.SCRAPY, keys.SCRAPY_CONCURRENT_REQUESTS),
            'CONCURRENT_REQUESTS_PER_DOMAIN': self.get(keys.SCRAPY, keys.SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN),
            'CONCURRENT_REQUESTS_PER_IP': self.get(keys.SCRAPY, keys.SCRAPY_CONCURRENT_REQUESTS_PER_IP),
            'DOWNLOAD_DELAY': self.get(keys.SCRAPY, keys.SCRAPY_DOWNLOAD_DELAY),
            'DOWNLOAD_TIMEOUT': self.get(keys.SCRAPY, keys.SCRAPY_DOWNLOAD_TIMEOUT),
            'DNS_TIMEOUT': self.get(keys.SCRAPY, keys.SCRAPY_DNS_TIMEOUT),
            'RETRY_ENABLED': self.get(keys.SCRAPY, keys.SCRAPY_RETRY_ENABLED),
            'RETRY_TIMES': self.get(keys.SCRAPY, keys.SCRAPY_RETRY_TIMES),
            'ROBOTSTXT_OBEY': self.get(keys.SCRAPY, keys.SCRAPY_ROBOTSTXT_OBEY),
            'USER_AGENT': self.get(keys.SCRAPY, keys.SCRAPY_USER_AGENT),
            'LOG_LEVEL': self.get(keys.SCRAPY, keys.SCRAPY_LOG_LEVEL),
            'REQUEST_FINGERPRINTER_IMPLEMENTATION': self.get(keys.SCRAPY, keys.SCRAPY_REQUEST_FINGERPRINTER_IMPLEMENTATION),
            'HTTPERROR_ALLOW_ALL': True,
        }

    def get_stage1_config(self) -> Dict[str, Any]:
        """Get Stage 1 discovery configuration"""
        return {
            keys.DISCOVERY_MAX_DEPTH: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH),
            keys.DISCOVERY_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_OUTPUT_FILE),
            keys.DISCOVERY_SEED_FILE: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_SEED_FILE),
        }

    def get_stage2_config(self) -> Dict[str, Any]:
        """Get Stage 2 validation configuration"""
        return {
            keys.VALIDATION_MAX_WORKERS: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_MAX_WORKERS),
            keys.VALIDATION_TIMEOUT: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_TIMEOUT),
            keys.VALIDATION_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_OUTPUT_FILE),
        }

    def get_stage3_config(self) -> Dict[str, Any]:
        """Get Stage 3 enrichment configuration"""
        return {
            keys.ENRICHMENT_NLP_ENABLED: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_NLP_ENABLED),
            keys.ENRICHMENT_MAX_TEXT_LENGTH: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_MAX_TEXT_LENGTH),
            keys.ENRICHMENT_TOP_KEYWORDS: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_TOP_KEYWORDS),
            keys.ENRICHMENT_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_OUTPUT_FILE),
        }

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration"""
        return {
            keys.LOGGING_LEVEL: self.get(keys.LOGGING, keys.LOGGING_LEVEL, default='INFO'),
            keys.LOGGING_FORMAT: self.get(keys.LOGGING, keys.LOGGING_FORMAT, default='%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            keys.LOGGING_FILE: self.get(keys.LOGGING, keys.LOGGING_FILE),
            'max_bytes': self.get(keys.LOGGING, 'max_bytes', default=10485760),
            'backup_count': self.get(keys.LOGGING, 'backup_count', default=3),
        }

    def get_data_paths(self) -> Dict[str, Path]:
        """Get data directory paths"""
        return {
            keys.RAW_DIR: Path(self.get(keys.DATA, keys.RAW_DIR)),
            keys.PROCESSED_DIR: Path(self.get(keys.DATA, keys.PROCESSED_DIR)),
            keys.CATALOG_DIR: Path(self.get(keys.DATA, keys.CATALOG_DIR)),
            keys.CACHE_DIR: Path(self.get(keys.DATA, keys.CACHE_DIR)),
            keys.EXPORTS_DIR: Path(self.get(keys.DATA, keys.EXPORTS_DIR)),
            keys.LOGS_DIR: Path(self.get(keys.DATA, keys.LOGS_DIR)),
            keys.TEMP_DIR: Path(self.get(keys.DATA, keys.TEMP_DIR, default='data/temp')),
        }

    @property
    def environment(self) -> str:
        """Get current environment"""
        return self.env
