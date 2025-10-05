import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.common import config_keys as keys
from src.common.config_schema import PipelineConfig

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails"""
    pass


class Config:
    """Configuration manager with Pydantic-based validation (fail-fast on errors)"""
    config_dir = Path(__file__).parent.parent.parent / 'config'

    def __init__(self, env: str = 'development', validate: bool = True):
        self.env = env
        self.config_dir = self.config_dir
        self._raw_config = self._load_config()

        if validate:
            # Validate configuration using Pydantic schema (fail-fast)
            self._validated_config = self._validate_with_pydantic()
            self._config = self._validated_config.to_dict()
        else:
            # Skip validation (not recommended)
            self._config = self._raw_config
            self._validated_config = None
            logger.warning("Configuration validation is disabled. This is not recommended.")

    def _load_config(self) -> dict[str, Any]:
        """Load configuration from YAML file because JSON wasn't good enough"""
        config_file = self.config_dir / f'{self.env}.yml'

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_file) as f:
            config = yaml.safe_load(f)

        config = self._apply_env_overrides(config)

        return config

    def _apply_env_overrides(self, config: dict[str, Any]) -> dict[str, Any]:
        """Apply environment variable overrides with type coercion"""
        env_overrides = {
            'SCRAPY_CONCURRENT_REQUESTS': ([keys.SCRAPY, keys.SCRAPY_CONCURRENT_REQUESTS], int),
            'SCRAPY_DOWNLOAD_DELAY': ([keys.SCRAPY, keys.SCRAPY_DOWNLOAD_DELAY], float),
            'STAGE1_MAX_DEPTH': ([keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH], int),
        }

        for env_var, (config_path, value_type) in env_overrides.items():
            if env_var in os.environ:
                raw_value = os.environ[env_var]
                try:
                    # Type coercion based on expected type
                    value = value_type(raw_value)
                except (ValueError, TypeError) as e:
                    logger.warning(
                        f"Failed to convert env var {env_var}='{raw_value}' to {value_type.__name__}: {e}. "
                        f"Using raw string value."
                    )
                    value = raw_value

                current = config
                for key in config_path[:-1]:
                    current = current.setdefault(key, {})
                current[config_path[-1]] = value

        return config

    def _validate_with_pydantic(self) -> PipelineConfig:
        """
        Validate configuration using Pydantic schema.
        Fails fast with detailed error messages on validation failure.

        Returns:
            PipelineConfig: Validated configuration object

        Raises:
            ConfigValidationError: If validation fails
        """
        try:
            # Pydantic will validate types, ranges, and reject unknown keys
            validated = PipelineConfig.from_dict(self._raw_config)
            logger.info(f"Configuration validation passed for environment: {self.env}")
            return validated

        except ValidationError as e:
            # Format Pydantic errors into readable messages
            error_lines = ["Configuration validation failed:", ""]

            for error in e.errors():
                location = " -> ".join(str(loc) for loc in error['loc'])
                msg = error['msg']
                error_type = error['type']

                # Add helpful context based on error type
                if 'extra_forbidden' in error_type:
                    field_name = error['loc'][-1] if error['loc'] else 'unknown'
                    error_lines.append(
                        f"  ❌ Unknown key '{field_name}' at {location}"
                    )
                    error_lines.append(
                        "     This might be a typo. Check your configuration file."
                    )
                elif 'type_error' in error_type:
                    error_lines.append(f"  ❌ Type error at {location}: {msg}")
                    if 'input_value' in error:
                        error_lines.append(f"     Got: {error['input_value']} (type: {type(error['input_value']).__name__})")
                elif 'value_error' in error_type:
                    error_lines.append(f"  ❌ Value error at {location}: {msg}")
                else:
                    error_lines.append(f"  ❌ Error at {location}: {msg}")

                error_lines.append("")

            error_message = "\n".join(error_lines)
            logger.error(error_message)
            raise ConfigValidationError(error_message) from e

        except ValueError as e:
            # Catch custom validation errors from PipelineConfig.from_dict
            error_message = f"Configuration validation failed:\n\n  ❌ {str(e)}"
            logger.error(error_message)
            raise ConfigValidationError(error_message) from e

        except Exception as e:
            # Catch any other unexpected errors
            error_message = f"Unexpected error during configuration validation: {str(e)}"
            logger.error(error_message)
            raise ConfigValidationError(error_message) from e

    def _validate_config(self) -> None:
        """
        Legacy validation method - now replaced by Pydantic validation.
        Kept for backward compatibility but does nothing.
        """
        # Validation is now handled by _validate_with_pydantic()
        pass

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get configuration value using dot notation"""
        current = self._config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def get_scrapy_settings(self) -> dict[str, Any]:
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

    def get_stage1_config(self) -> dict[str, Any]:
        """Get Stage 1 discovery configuration"""
        return {
            keys.DISCOVERY_ALLOWED_DOMAINS: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_ALLOWED_DOMAINS),
            keys.DISCOVERY_MAX_DEPTH: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_MAX_DEPTH),
            keys.DISCOVERY_BATCH_SIZE: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_BATCH_SIZE, default=100),
            keys.DISCOVERY_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_OUTPUT_FILE),
            keys.DISCOVERY_SEED_FILE: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_SEED_FILE),
            keys.DISCOVERY_HEADLESS_BROWSER: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_HEADLESS_BROWSER, default={}),
            keys.DISCOVERY_HEURISTICS: self.get(keys.STAGES, keys.STAGE_DISCOVERY, keys.DISCOVERY_HEURISTICS, default={}),
        }

    def get_stage2_config(self) -> dict[str, Any]:
        """Get Stage 2 validation configuration"""
        return {
            keys.VALIDATION_MAX_WORKERS: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_MAX_WORKERS),
            keys.VALIDATION_TIMEOUT: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_TIMEOUT),
            keys.VALIDATION_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_VALIDATION, keys.VALIDATION_OUTPUT_FILE),
        }

    def get_stage3_config(self) -> dict[str, Any]:
        """Get Stage 3 enrichment configuration"""
        return {
            keys.ENRICHMENT_ALLOWED_DOMAINS: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_ALLOWED_DOMAINS),
            keys.ENRICHMENT_NLP_ENABLED: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_NLP_ENABLED),
            keys.ENRICHMENT_MAX_TEXT_LENGTH: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_MAX_TEXT_LENGTH),
            keys.ENRICHMENT_TOP_KEYWORDS: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_TOP_KEYWORDS),
            keys.ENRICHMENT_OUTPUT_FILE: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_OUTPUT_FILE),
            keys.ENRICHMENT_HEADLESS_BROWSER: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_HEADLESS_BROWSER, default={}),
            keys.ENRICHMENT_CONTENT_TYPES: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_CONTENT_TYPES, default={}),
            keys.ENRICHMENT_STORAGE: self.get(keys.STAGES, keys.STAGE_ENRICHMENT, keys.ENRICHMENT_STORAGE, default={}),
        }

    def get_nlp_config(self) -> dict[str, Any]:
        """Get NLP configuration"""
        return {
            keys.NLP_SPACY_MODEL: self.get(keys.NLP, keys.NLP_SPACY_MODEL, default='en_core_web_sm'),
            keys.NLP_USE_TRANSFORMERS: self.get(keys.NLP, keys.NLP_USE_TRANSFORMERS, default=False),
            keys.NLP_TRANSFORMER_NER_MODEL: self.get(keys.NLP, keys.NLP_TRANSFORMER_NER_MODEL, default='dslim/bert-base-NER'),
            keys.NLP_SUMMARIZER_MODEL: self.get(keys.NLP, keys.NLP_SUMMARIZER_MODEL, default='sshleifer/distilbart-cnn-12-6'),
            keys.NLP_MAX_TEXT_LENGTH: self.get(keys.NLP, keys.NLP_MAX_TEXT_LENGTH, default=20000),
            keys.NLP_TOP_KEYWORDS: self.get(keys.NLP, keys.NLP_TOP_KEYWORDS, default=15),
            keys.NLP_SUMMARY_MAX_LENGTH: self.get(keys.NLP, keys.NLP_SUMMARY_MAX_LENGTH, default=150),
            keys.NLP_SUMMARY_MIN_LENGTH: self.get(keys.NLP, keys.NLP_SUMMARY_MIN_LENGTH, default=30),
            keys.NLP_DEVICE: self.get(keys.NLP, keys.NLP_DEVICE, default='auto'),
        }

    def get_logging_config(self) -> dict[str, Any]:
        """Get logging configuration"""
        return {
            keys.LOGGING_LEVEL: self.get(keys.LOGGING, keys.LOGGING_LEVEL, default='INFO'),
            keys.LOGGING_FORMAT: self.get(keys.LOGGING, keys.LOGGING_FORMAT, default='%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            keys.LOGGING_FILE: self.get(keys.LOGGING, keys.LOGGING_FILE),
            'max_bytes': self.get(keys.LOGGING, 'max_bytes', default=10485760),
            'backup_count': self.get(keys.LOGGING, 'backup_count', default=3),
        }

    def get_data_paths(self) -> dict[str, Path]:
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