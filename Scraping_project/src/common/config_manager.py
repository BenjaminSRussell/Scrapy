"""
Unified Configuration Manager

Consolidates configuration from multiple sources with clear precedence:
1. Environment Variables (highest priority)
2. YAML Configuration Files
3. Code Defaults (lowest priority)

Features:
- Type-safe configuration access with Pydantic models
- Automatic validation on load
- Clear separation of concerns (database, redis, kafka, stages)
- Single source of truth for all configuration
- Easy testing with override capability

Usage:
    from src.common.config_manager import ConfigManager

    config = ConfigManager.get_instance()
    db_config = config.database
    redis_config = config.redis

    # Access nested values
    js_threshold = config.stage1.js_confidence_threshold
"""

import os
import yaml
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field, validator
from enum import Enum


class Environment(str, Enum):
    """Deployment environment"""
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"


# ============================================================================
# Configuration Models (Type-Safe with Validation)
# ============================================================================

class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration"""
    host: str = "localhost"
    port: int = 5432
    database: str = "scraping_db"
    user: str = "postgres"
    password: Optional[str] = None
    min_connections: int = 1
    max_connections: int = 10

    @validator('password')
    def validate_password(cls, v):
        """Warn if password is not set in production"""
        if v is None:
            import warnings
            warnings.warn("Database password not set. Connection may fail.")
        return v


class RedisConfig(BaseModel):
    """Redis configuration"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 50
    url: Optional[str] = None  # Alternative: redis://host:port/db

    @property
    def is_fakeredis(self) -> bool:
        """Check if using fakeredis for testing"""
        return self.url is not None and self.url.startswith("fakeredis://")


class KafkaProducerConfig(BaseModel):
    """Kafka producer configuration"""
    compression_type: str = "gzip"
    acks: str = "all"
    retries: int = 3
    max_in_flight_requests_per_connection: int = 5


class KafkaConsumerConfig(BaseModel):
    """Kafka consumer configuration"""
    group_id: str = "scraping-pipeline"
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = True


class KafkaConfig(BaseModel):
    """Kafka configuration"""
    bootstrap_servers: str = "localhost:9092"
    topic: str = "scraped-items"
    dead_letter_topic: str = "dead-letter-queue"
    producer: KafkaProducerConfig = Field(default_factory=KafkaProducerConfig)
    consumer: KafkaConsumerConfig = Field(default_factory=KafkaConsumerConfig)

    # SASL/SSL configuration
    security_protocol: Optional[str] = None
    sasl_mechanism: Optional[str] = None
    sasl_username: Optional[str] = None
    sasl_password: Optional[str] = None


class SpiderConfig(BaseModel):
    """Individual spider configuration"""
    concurrent_requests: int = 32
    download_delay: float = 0.1
    depth_limit: int = 5
    depth_priority: int = 0
    scheduler_disk_queue: str = "scrapy.squeues.PickleFifoDiskQueue"
    scheduler_memory_queue: str = "scrapy.squeues.FifoMemoryQueue"


class Stage1Config(BaseModel):
    """Stage 1 (Discovery/Crawling) configuration"""
    allowed_domains: list[str] = Field(default_factory=list)
    js_confidence_threshold: float = 0.7
    batch_size: int = 50
    js_queue_batch_size: int = 20
    circuit_breaker_enabled: bool = True
    circuit_breaker_error_threshold: int = 5
    use_redis_queue: bool = True
    priority_boost_keywords: list[str] = Field(default_factory=lambda: [
        "research", "publications", "faculty", "staff", "departments"
    ])
    priority_penalty_keywords: list[str] = Field(default_factory=lambda: [
        "login", "logout", "cart", "checkout", "wp-admin"
    ])
    spiders: dict[str, SpiderConfig] = Field(default_factory=dict)


class Stage2Config(BaseModel):
    """Stage 2 (Page Analysis) configuration"""
    max_workers: int = 10
    batch_size: int = 100
    poll_interval_seconds: int = 60
    min_word_count: int = 100
    min_text_to_html_ratio: float = 0.1
    massive_doc_threshold: int = 50000
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_on_status_codes: list[int] = Field(default_factory=lambda: [500, 502, 503, 504])


class Stage3Config(BaseModel):
    """Stage 3 (Summarization) configuration"""
    max_workers: int = 5
    batch_size: int = 50
    poll_interval_seconds: int = 120
    similarity_threshold: float = 0.85
    model_name: str = "facebook/bart-large-cnn"
    max_length: int = 150
    min_length: int = 50
    device: str = "cpu"


class Stage4Config(BaseModel):
    """Stage 4 (Large Document Processing) configuration"""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    model_name: str = "facebook/bart-large-cnn"
    max_summary_length: int = 200
    min_summary_length: int = 75
    device: str = "cpu"
    max_workers: int = 3


class DeltaLakeConfig(BaseModel):
    """Delta Lake configuration"""
    base_path: str = "./data/delta_lake"
    checkpoint_interval: int = 100
    force_shutdown_timeout: int = 30
    queue_maxsize: int = 1000

    # Table names
    tables: dict[str, str] = Field(default_factory=lambda: {
        "seed_urls": "seed_urls",
        "stage1_discovery": "stage1_discovery",
        "stage1_errors": "stage1_errors",
        "stage1_offsite_candidates": "stage1_offsite_candidates",
        "js_spider_queue": "js_spider_queue",
        "stage2_queue": "stage2_queue",
        "stage2_page_analysis": "stage2_page_analysis",
        "stage3_analytics": "stage3_analytics",
        "stage3_summaries": "stage3_summaries",
        "stage4_large_docs": "stage4_large_docs",
        "stage4_summaries": "stage4_summaries",
    })


class LoggingConfig(BaseModel):
    """Logging configuration"""
    level: str = "INFO"
    format: str = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    file: Optional[str] = None
    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


class MonitoringConfig(BaseModel):
    """Monitoring configuration (Prometheus/Grafana)"""
    enabled: bool = True
    prometheus_port: int = 9090
    grafana_port: int = 3000
    metrics_port: int = 8000
    metrics_interval: int = 60

    # Metrics to collect
    metrics: list[str] = Field(default_factory=lambda: [
        "urls_processed",
        "urls_discovered",
        "errors_encountered",
        "processing_time",
        "queue_size",
    ])


class ExportConfig(BaseModel):
    """Data export configuration"""
    default_format: str = "parquet"
    output_directory: str = "./data/exports"
    compression: str = "snappy"


# ============================================================================
# Main Configuration Container
# ============================================================================

class AppConfig(BaseModel):
    """Complete application configuration"""
    environment: Environment = Environment.DEVELOPMENT

    # Component configurations
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    delta_lake: DeltaLakeConfig = Field(default_factory=DeltaLakeConfig)

    # Stage configurations
    stage1: Stage1Config = Field(default_factory=Stage1Config)
    stage2: Stage2Config = Field(default_factory=Stage2Config)
    stage3: Stage3Config = Field(default_factory=Stage3Config)
    stage4: Stage4Config = Field(default_factory=Stage4Config)

    # Infrastructure
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    class Config:
        arbitrary_types_allowed = True


# ============================================================================
# Configuration Manager (Singleton)
# ============================================================================

class ConfigManager:
    """
    Unified configuration manager with clear precedence and validation.

    Configuration Load Order (highest to lowest priority):
    1. Environment Variables
    2. YAML Configuration File (config/{environment}.yml or config.yml)
    3. Pydantic Model Defaults

    Usage:
        config = ConfigManager.get_instance()
        db_host = config.database.host
        redis_port = config.redis.port
    """

    _instance: Optional['ConfigManager'] = None
    _config: Optional[AppConfig] = None

    def __init__(self, config_path: Optional[Path] = None, env_override: Optional[Environment] = None):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to YAML config file. If None, auto-detects based on ENV.
            env_override: Override environment (useful for testing)
        """
        self._config_path = config_path
        self._env_override = env_override
        self._load_config()

    @classmethod
    def get_instance(cls, reset: bool = False) -> 'ConfigManager':
        """Get singleton instance"""
        if cls._instance is None or reset:
            cls._instance = cls()
        return cls._instance

    def _load_config(self) -> None:
        """Load configuration from all sources with proper precedence"""
        # Step 1: Determine environment
        env = self._env_override or os.getenv("ENV", "development")
        environment = Environment(env)

        # Step 2: Load YAML configuration
        yaml_config = self._load_yaml_config(environment)

        # Step 3: Override with environment variables
        config_dict = self._apply_env_overrides(yaml_config)

        # Step 4: Set environment
        config_dict['environment'] = environment

        # Step 5: Validate and create config object
        self._config = AppConfig(**config_dict)

    def _load_yaml_config(self, environment: Environment) -> dict[str, Any]:
        """Load YAML configuration file"""
        if self._config_path:
            config_path = self._config_path
        else:
            # Auto-detect config path
            project_root = Path(__file__).parent.parent.parent

            # Try environment-specific config first
            env_config = project_root / "config" / f"{environment.value}.yml"
            if env_config.exists():
                config_path = env_config
            else:
                # Fall back to generic config.yml
                config_path = project_root / "config.yml"

        if not config_path.exists():
            print(f"Warning: Config file not found at {config_path}, using defaults")
            return {}

        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}

    def _apply_env_overrides(self, yaml_config: dict[str, Any]) -> dict[str, Any]:
        """Apply environment variable overrides to configuration"""
        config = yaml_config.copy()

        # Database configuration (support both DB_* and POSTGRES_* prefixes)
        db_config = config.get('database', {}) if 'database' in config else config.get('postgres', {})
        db_config['host'] = os.getenv('DB_HOST') or os.getenv('POSTGRES_HOST') or db_config.get('host', 'localhost')
        db_config['port'] = int(os.getenv('DB_PORT') or os.getenv('POSTGRES_PORT') or db_config.get('port', 5432))
        db_config['database'] = os.getenv('DB_NAME') or os.getenv('POSTGRES_DB') or db_config.get('database', 'scraping_db')
        db_config['user'] = os.getenv('DB_USER') or os.getenv('POSTGRES_USER') or db_config.get('user', 'postgres')
        db_config['password'] = os.getenv('DB_PASSWORD') or os.getenv('POSTGRES_PASSWORD') or db_config.get('password')
        config['database'] = db_config

        # Redis configuration
        redis_config = config.get('redis', {})
        redis_config['url'] = os.getenv('REDIS_URL') or redis_config.get('url')
        redis_config['host'] = os.getenv('REDIS_HOST') or redis_config.get('host', 'localhost')
        redis_config['port'] = int(os.getenv('REDIS_PORT') or redis_config.get('port', 6379))
        redis_config['password'] = os.getenv('REDIS_PASSWORD') or redis_config.get('password')
        config['redis'] = redis_config

        # Kafka configuration
        kafka_config = config.get('kafka', {})
        kafka_config['bootstrap_servers'] = os.getenv('KAFKA_BOOTSTRAP_SERVERS') or kafka_config.get('bootstrap_servers', 'localhost:9092')
        kafka_config['topic'] = os.getenv('KAFKA_TOPIC') or kafka_config.get('topic', 'scraped-items')

        # SASL/SSL configuration
        kafka_config['security_protocol'] = os.getenv('KAFKA_SECURITY_PROTOCOL') or kafka_config.get('security_protocol')
        kafka_config['sasl_mechanism'] = os.getenv('KAFKA_SASL_MECHANISM') or kafka_config.get('sasl_mechanism')
        kafka_config['sasl_username'] = os.getenv('KAFKA_SASL_USERNAME') or kafka_config.get('sasl_username')
        kafka_config['sasl_password'] = os.getenv('KAFKA_SASL_PASSWORD') or kafka_config.get('sasl_password')
        config['kafka'] = kafka_config

        # Logging configuration
        logging_config = config.get('logging', {})
        logging_config['level'] = os.getenv('LOG_LEVEL') or logging_config.get('level', 'INFO')
        config['logging'] = logging_config

        # Monitoring configuration
        monitoring_config = config.get('monitoring', {})
        monitoring_config['metrics_port'] = int(os.getenv('METRICS_PORT') or monitoring_config.get('metrics_port', 8000))
        monitoring_config['metrics_interval'] = int(os.getenv('METRICS_INTERVAL') or monitoring_config.get('metrics_interval', 60))
        config['monitoring'] = monitoring_config

        return config

    @property
    def config(self) -> AppConfig:
        """Get the complete configuration object"""
        if self._config is None:
            raise RuntimeError("Configuration not loaded")
        return self._config

    # Convenience properties for common configurations

    @property
    def database(self) -> DatabaseConfig:
        """Database configuration"""
        return self.config.database

    @property
    def redis(self) -> RedisConfig:
        """Redis configuration"""
        return self.config.redis

    @property
    def kafka(self) -> KafkaConfig:
        """Kafka configuration"""
        return self.config.kafka

    @property
    def delta_lake(self) -> DeltaLakeConfig:
        """Delta Lake configuration"""
        return self.config.delta_lake

    @property
    def stage1(self) -> Stage1Config:
        """Stage 1 configuration"""
        return self.config.stage1

    @property
    def stage2(self) -> Stage2Config:
        """Stage 2 configuration"""
        return self.config.stage2

    @property
    def stage3(self) -> Stage3Config:
        """Stage 3 configuration"""
        return self.config.stage3

    @property
    def stage4(self) -> Stage4Config:
        """Stage 4 configuration"""
        return self.config.stage4

    @property
    def logging(self) -> LoggingConfig:
        """Logging configuration"""
        return self.config.logging

    @property
    def monitoring(self) -> MonitoringConfig:
        """Monitoring configuration"""
        return self.config.monitoring

    @property
    def export(self) -> ExportConfig:
        """Export configuration"""
        return self.config.export

    def reload(self) -> None:
        """Reload configuration from sources"""
        self._load_config()

    def to_dict(self) -> dict[str, Any]:
        """Export configuration as dictionary"""
        return self.config.dict()

    def to_yaml(self, file_path: Optional[Path] = None) -> str:
        """
        Export configuration as YAML.

        Args:
            file_path: Optional file path to write YAML to

        Returns:
            YAML string representation
        """
        yaml_str = yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

        if file_path:
            with open(file_path, 'w') as f:
                f.write(yaml_str)

        return yaml_str


# ============================================================================
# Backward Compatibility Layer
# ============================================================================

class Config:
    """
    Legacy Config class for backward compatibility.

    Deprecated: Use ConfigManager.get_instance() instead.
    """

    @staticmethod
    def get_instance():
        """Get ConfigManager instance (backward compatible)"""
        import warnings
        warnings.warn(
            "Config.get_instance() is deprecated. Use ConfigManager.get_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return ConfigManager.get_instance()


# ============================================================================
# Convenience Functions
# ============================================================================

def get_config() -> ConfigManager:
    """Get configuration manager instance"""
    return ConfigManager.get_instance()


def get_database_config() -> DatabaseConfig:
    """Get database configuration"""
    return get_config().database


def get_redis_config() -> RedisConfig:
    """Get Redis configuration"""
    return get_config().redis


def get_kafka_config() -> KafkaConfig:
    """Get Kafka configuration"""
    return get_config().kafka
