"""
Global configuration management.

Consolidated from:
- src/common/config.py
- src/common/config_manager.py

Provides unified configuration access with YAML file support and sensible defaults.
"""

from pathlib import Path
from typing import Any, Optional
import yaml
import logging
import os

logger = logging.getLogger(__name__)


class Config:
    """Global configuration manager with singleton pattern."""

    _instance: Optional['Config'] = None

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to YAML config file. Defaults to project_root/config.yml
        """
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config.yml"

        self.config_path = Path(config_path)
        self._config: dict = {}
        self.load()

    def load(self) -> None:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}, using defaults")
            self._config = self._default_config()
            return

        try:
            with open(self.config_path) as f:
                self._config = yaml.safe_load(f) or {}
            logger.info(f"Configuration loaded from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}, using defaults")
            self._config = self._default_config()

    def _default_config(self) -> dict:
        """Default configuration."""
        return {
            "redis": {
                "host": os.getenv("REDIS_HOST", "localhost"),
                "port": int(os.getenv("REDIS_PORT", 6379)),
                "db": 0
            },
            "delta_lake": {
                "base_path": "./data/delta_lake"
            },
            "stages": {
                "stage1": {
                    "url_limit": 100,
                    "concurrent_requests": 512
                },
                "stage2": {
                    "concurrent": 50,
                    "poll_interval": 3
                },
                "stage3": {
                    "concurrent": 20,
                    "poll_interval": 5
                },
                "stage4": {
                    "enabled": True
                }
            }
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config value by dot notation key.

        Args:
            key: Dot-notation key (e.g., "redis.host")
            default: Default value if key not found

        Returns:
            Config value or default

        Example:
            config = get_config()
            redis_host = config.get("redis.host", "localhost")
        """
        keys = key.split('.')
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value if value is not None else default

    def set(self, key: str, value: Any) -> None:
        """
        Set config value by dot notation key.

        Args:
            key: Dot-notation key (e.g., "redis.host")
            value: Value to set

        Example:
            config = get_config()
            config.set("redis.host", "redis.example.com")
        """
        keys = key.split('.')
        config = self._config

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value

    def get_section(self, section: str) -> dict:
        """
        Get entire config section.

        Args:
            section: Section name

        Returns:
            Dictionary of section config

        Example:
            config = get_config()
            redis_config = config.get_section("redis")
        """
        return self._config.get(section, {})

    def reload(self) -> None:
        """Reload configuration from file."""
        self.load()

    @classmethod
    def get_instance(cls, config_path: Optional[Path] = None) -> 'Config':
        """
        Get singleton instance of Config.

        Args:
            config_path: Optional path to config file

        Returns:
            Config instance
        """
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for testing)."""
        cls._instance = None

    def get_raw_config(self) -> dict:
        """Get raw config dictionary."""
        return self._config.copy()


# Global singleton
_config_instance: Optional[Config] = None


def get_config(config_path: Optional[Path] = None) -> Config:
    """
    Get global config instance.

    This is the primary way to access configuration throughout the pipeline.

    Args:
        config_path: Optional path to config file

    Returns:
        Config instance

    Example:
        from src.core.config import get_config

        config = get_config()
        redis_host = config.get("redis.host")
        stage2_workers = config.get("stages.stage2.concurrent", 50)
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance


def reset_config():
    """Reset global config instance (useful for testing)."""
    global _config_instance
    _config_instance = None
    Config.reset_instance()
