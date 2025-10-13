"""Configuration Loader - Single source of truth for all pipeline settings."""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class Config:
    """Configuration manager for the scraping pipeline."""

    def __init__(self, config_path: str | None = None):
        """Load configuration from YAML file.

        Args:
            config_path: Path to config.yml (defaults to project root)
        """
        if config_path is None:
            # Default to config.yml in project root
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
        """Get default configuration if file doesn't exist."""
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
        """Get configuration value using dot notation.

        Args:
            key_path: Dot-separated path (e.g., 'redis.host')
            default: Default value if key not found

        Returns:
            Configuration value

        Example:
            >>> config.get('redis.host')
            'localhost'
            >>> config.get('stage1.concurrent_requests')
            512
        """
        keys = key_path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """Get entire configuration section.

        Args:
            section: Section name (e.g., 'redis', 'stage1')

        Returns:
            Dictionary of section configuration
        """
        return self._config.get(section, {})

    def set(self, key_path: str, value: Any):
        """Set configuration value (runtime only, not persisted).

        Args:
            key_path: Dot-separated path
            value: Value to set
        """
        keys = key_path.split('.')
        config = self._config

        # Navigate to parent
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        # Set value
        config[keys[-1]] = value

    def reload(self):
        """Reload configuration from file."""
        self._load_config()

    # ============================================
    # Convenience Methods for Common Settings
    # ============================================

    @property
    def redis_config(self) -> dict[str, Any]:
        """Get Redis configuration."""
        return self.get_section('redis')

    @property
    def postgres_config(self) -> dict[str, Any]:
        """Get PostgreSQL configuration."""
        return self.get_section('postgres')

    @property
    def stage1_config(self) -> dict[str, Any]:
        """Get Stage 1 configuration."""
        return self.get_section('stage1')

    @property
    def stage2_config(self) -> dict[str, Any]:
        """Get Stage 2 configuration."""
        return self.get_section('stage2')

    @property
    def stage3_config(self) -> dict[str, Any]:
        """Get Stage 3 configuration."""
        return self.get_section('stage3')

    @property
    def stage4_config(self) -> dict[str, Any]:
        """Get Stage 4 configuration."""
        return self.get_section('stage4')

    @property
    def delta_lake_config(self) -> dict[str, Any]:
        """Get Delta Lake configuration."""
        return self.get_section('delta_lake')

    @property
    def message_queue_config(self) -> dict[str, Any]:
        """Get message queue configuration."""
        return self.get_section('message_queues')


# Global configuration instance
_config: Config | None = None


def get_config(config_path: str | None = None) -> Config:
    """Get global configuration instance.

    Args:
        config_path: Optional path to config file

    Returns:
        Config instance
    """
    global _config

    if _config is None:
        _config = Config(config_path)

    return _config


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration and return as dictionary.

    Args:
        config_path: Optional path to config file

    Returns:
        Configuration dictionary
    """
    config = get_config(config_path)
    return config._config


def reload_config():
    """Reload global configuration from file."""
    global _config
    if _config:
        _config.reload()
