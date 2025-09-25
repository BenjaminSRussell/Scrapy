import os
import yaml
from pathlib import Path
from typing import Dict, Any


class Config:
    """Configuration manager that reads env + stage YAML files"""

    def __init__(self, env: str = 'development'):
        self.env = env
        self.config_dir = Path(__file__).parent.parent.parent / 'config'
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        config_file = self.config_dir / f'{self.env}.yml'

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        # Override with environment variables if they exist
        config = self._apply_env_overrides(config)

        return config

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides"""
        # Examples of environment variable overrides
        env_overrides = {
            'SCRAPY_CONCURRENT_REQUESTS': ['scrapy', 'concurrent_requests'],
            'SCRAPY_DOWNLOAD_DELAY': ['scrapy', 'download_delay'],
            'STAGE1_MAX_DEPTH': ['stages', 'discovery', 'max_depth'],
            'STAGE1_BATCH_SIZE': ['stages', 'discovery', 'batch_size'],
        }

        for env_var, config_path in env_overrides.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                # Try to convert to appropriate type
                try:
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    # Keep as string if not a number
                    pass

                # Navigate to the config path and set the value
                current = config
                for key in config_path[:-1]:
                    current = current.setdefault(key, {})
                current[config_path[-1]] = value

        return config

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
            'CONCURRENT_REQUESTS': self.get('scrapy', 'concurrent_requests'),
            'CONCURRENT_REQUESTS_PER_DOMAIN': self.get('scrapy', 'concurrent_requests_per_domain'),
            'CONCURRENT_REQUESTS_PER_IP': self.get('scrapy', 'concurrent_requests_per_ip'),
            'DOWNLOAD_DELAY': self.get('scrapy', 'download_delay'),
            'DOWNLOAD_TIMEOUT': self.get('scrapy', 'download_timeout'),
            'DNS_TIMEOUT': self.get('scrapy', 'dns_timeout'),
            'RETRY_ENABLED': self.get('scrapy', 'retry_enabled'),
            'RETRY_TIMES': self.get('scrapy', 'retry_times'),
            'ROBOTSTXT_OBEY': self.get('scrapy', 'robotstxt_obey'),
            'USER_AGENT': self.get('scrapy', 'user_agent'),
            'LOG_LEVEL': self.get('scrapy', 'log_level'),
            'HTTPERROR_ALLOW_ALL': True,
        }

    def get_stage1_config(self) -> Dict[str, Any]:
        """Get Stage 1 discovery configuration"""
        return {
            'max_depth': self.get('stages', 'discovery', 'max_depth'),
            'batch_size': self.get('stages', 'discovery', 'batch_size'),
            'output_file': self.get('stages', 'discovery', 'output_file'),
        }

    def get_stage2_config(self) -> Dict[str, Any]:
        """Get Stage 2 validation configuration"""
        return {
            'max_workers': self.get('stages', 'validation', 'max_workers'),
            'timeout': self.get('stages', 'validation', 'timeout'),
            'output_file': self.get('stages', 'validation', 'output_file'),
        }

    def get_stage3_config(self) -> Dict[str, Any]:
        """Get Stage 3 enrichment configuration"""
        return {
            'nlp_enabled': self.get('stages', 'enrichment', 'nlp_enabled'),
            'max_text_length': self.get('stages', 'enrichment', 'max_text_length'),
            'top_keywords': self.get('stages', 'enrichment', 'top_keywords'),
            'output_file': self.get('stages', 'enrichment', 'output_file'),
        }

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration"""
        return {
            'level': self.get('logging', 'level', default='INFO'),
            'format': self.get('logging', 'format', default='%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            'file': self.get('logging', 'file'),
            'max_bytes': self.get('logging', 'max_bytes', default=10485760),  # 10MB default
            'backup_count': self.get('logging', 'backup_count', default=3),
        }

    def get_data_paths(self) -> Dict[str, Path]:
        """Get data directory paths"""
        return {
            'raw_dir': Path(self.get('data', 'raw_dir')),
            'processed_dir': Path(self.get('data', 'processed_dir')),
            'catalog_dir': Path(self.get('data', 'catalog_dir')),
            'cache_dir': Path(self.get('data', 'cache_dir')),
            'exports_dir': Path(self.get('data', 'exports_dir')),
            'logs_dir': Path(self.get('data', 'logs_dir')),
        }

    @property
    def environment(self) -> str:
        """Get current environment"""
        return self.env