"""Load pipeline configuration from YAML with sensible defaults."""

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)


class Config:
    """Configuration manager for the scraping pipeline."""

    def __init__(self, config_path: str | None = None):
        """Load configuration from YAML file."""
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config.yml"

        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self):
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            logger.warning("Using default configuration")
            self._config = self._get_default_config()
            return

        try:
            with open(self.config_path) as f:
                self._config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            logger.warning("Using default configuration")
            self._config = self._get_default_config()

    def _get_default_config(self) -> dict[str, Any]:
        """Return baseline configuration when no file is present."""
        return {
            'redis': {
                'host': 'localhost',
                'port': 6379,
                'db': 0,
                'password': None,
            },
            'stage1': {
                'concurrent_requests': 512,
                'batch_size': 100,
                'circuit_breaker_enabled': True,
            },
            'stage2': {
                'max_workers': 100,
                'poll_interval_seconds': 3,
            },
            'stage3': {
                'max_workers': 50,
                'poll_interval_seconds': 5,
            },
        }

    def get(self, key_path: str, default: Any = None) -> Any:
        """Return a value using dot notation, or the provided default."""
        keys = key_path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """Return a nested configuration dictionary."""
        return self._config.get(section, {})

    def set(self, key_path: str, value: Any):
        """Set a configuration value for the current process."""
        keys = key_path.split('.')
        config = self._config

        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        config[keys[-1]] = value

    def reload(self):
        """Reload configuration from disk."""
        self._load_config()

    # ============================================
    # Convenience Methods for Common Settings
    # ============================================

    @property
    def redis_config(self) -> dict[str, Any]:
        """Return Redis configuration, prioritizing REDIS_URL env var."""
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            if redis_url == "fakeredis://":
                # Use default config for fakeredis, but signal its use
                config = self.get_section('redis').copy()
                config['is_fake'] = True
                return config

            parsed = urlparse(redis_url)
            return {
                'host': parsed.hostname,
                'port': parsed.port,
                'db': int(parsed.path.lstrip('/')) if parsed.path else 0,
                'password': parsed.password,
            }
        return self.get_section('redis')

    @property
    def postgres_config(self) -> dict[str, Any]:
        """Return PostgreSQL configuration block."""
        return self.get_section('postgres')

    @property
    def stage1_config(self) -> dict[str, Any]:
        """Return Stage 1 configuration block."""
        return self.get_section('stage1')

    @property
    def stage2_config(self) -> dict[str, Any]:
        """Return Stage 2 configuration block."""
        return self.get_section('stage2')

    @property
    def stage3_config(self) -> dict[str, Any]:
        """Return Stage 3 configuration block."""
        return self.get_section('stage3')

    @property
    def stage4_config(self) -> dict[str, Any]:
        """Return Stage 4 configuration block."""
        return self.get_section('stage4')

    @property
    def delta_lake_config(self) -> dict[str, Any]:
        """Return Delta Lake configuration block."""
        return self.get_section('delta_lake')

    @property
    def message_queue_config(self) -> dict[str, Any]:
        """Return message queue configuration block."""
        return self.get_section('message_queues')

    # Class-level instance for singleton pattern
    _instance: "Config | None" = None

    @classmethod
    def get_instance(cls, config_path: str | None = None) -> "Config":
        """
        Return the shared Config instance, creating it on first use.

        Note:
            Once created, the singleton instance persists. Subsequent calls with
            different parameters will return the existing instance without
            modification.
        """
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None

    def get_raw_config(self) -> dict[str, Any]:
        """Return the raw configuration dictionary."""
        return self._config
