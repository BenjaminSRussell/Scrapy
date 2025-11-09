import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Environment(str, Enum):

    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"

# ============================================================================
# ============================================================================

class DatabaseConfig(BaseModel):

    host: str = "localhost"
    port: int = 5432
    database: str = "scraping_db"
    user: str = "postgres"
    password: str | None = None
    min_connections: int = 1
    max_connections: int = 10

    @field_validator("password")
    def validate_password(cls, v: str | None) -> str | None:
        if v is None:
            import warnings

            warnings.warn("Database password not set. Connection may fail.", stacklevel=2)
        return v

class RedisConfig(BaseModel):

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None
    max_connections: int = 50
    url: str | None = None

    @property
    def is_fakeredis(self) -> bool:
        return self.url is not None and self.url.startswith("fakeredis://")

class KafkaProducerConfig(BaseModel):

    compression_type: str = "gzip"
    acks: str = "all"
    retries: int = 3
    max_in_flight_requests_per_connection: int = 5

class KafkaConsumerConfig(BaseModel):

    group_id: str = "scraping-pipeline"
    auto_offset_reset: str = "earliest"
    enable_auto_commit: bool = True

class KafkaConfig(BaseModel):

    bootstrap_servers: str = "localhost:9092"
    topic: str = "scraped-items"
    dead_letter_topic: str = "dead-letter-queue"
    producer: KafkaProducerConfig = Field(default_factory=KafkaProducerConfig)
    consumer: KafkaConsumerConfig = Field(default_factory=KafkaConsumerConfig)

    security_protocol: str | None = None
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None

class SpiderConfig(BaseModel):

    concurrent_requests: int = 32
    download_delay: float = 0.1
    depth_limit: int = 5
    depth_priority: int = 0
    scheduler_disk_queue: str = "scrapy.squeues.PickleFifoDiskQueue"
    scheduler_memory_queue: str = "scrapy.squeues.FifoMemoryQueue"

class Stage1Config(BaseModel):

    allowed_domains: list[str] = Field(default_factory=list)
    js_confidence_threshold: float = 0.7
    batch_size: int = 50
    js_queue_batch_size: int = 20
    circuit_breaker_enabled: bool = True
    circuit_breaker_error_threshold: int = 5
    use_redis_queue: bool = True
    priority_boost_keywords: list[str] = Field(
        default_factory=lambda: ["research", "publications", "faculty", "staff", "departments"]
    )
    priority_penalty_keywords: list[str] = Field(
        default_factory=lambda: ["login", "logout", "cart", "checkout", "wp-admin"]
    )
    spiders: dict[str, SpiderConfig] = Field(default_factory=dict)

class Stage2Config(BaseModel):

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

    max_workers: int = 5
    batch_size: int = 50
    poll_interval_seconds: int = 120
    similarity_threshold: float = 0.85
    model_name: str = "facebook/bart-large-cnn"
    max_length: int = 150
    min_length: int = 50
    device: str = "cpu"

class Stage4Config(BaseModel):

    chunk_size: int = 1000
    chunk_overlap: int = 200
    model_name: str = "facebook/bart-large-cnn"
    max_summary_length: int = 200
    min_summary_length: int = 75
    device: str = "cpu"
    max_workers: int = 3

class DeltaLakeConfig(BaseModel):

    base_path: str = "./data/delta_lake"
    checkpoint_interval: int = 100
    force_shutdown_timeout: int = 30
    queue_maxsize: int = 1000

    tables: dict[str, str] = Field(
        default_factory=lambda: {
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
        }
    )

class LoggingConfig(BaseModel):

    level: str = "INFO"
    format: str = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    file: str | None = None
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5

class MonitoringConfig(BaseModel):

    enabled: bool = True
    prometheus_port: int = 9090
    grafana_port: int = 3000
    metrics_port: int = 8000
    metrics_interval: int = 60

    metrics: list[str] = Field(
        default_factory=lambda: [
            "urls_processed",
            "urls_discovered",
            "errors_encountered",
            "processing_time",
            "queue_size",
        ]
    )

class ExportConfig(BaseModel):

    default_format: str = "parquet"
    output_directory: str = "./data/exports"
    compression: str = "snappy"

# ============================================================================
# ============================================================================

class MessageQueuesConfig(BaseModel):

    stage1_to_stage2: str = "stage1_discovered_urls"
    stage2_to_stage3: str = "stage2_analyzed_pages"
    stage2_to_stage4: str = "stage2_large_docs"
    js_render_queue: str = "stage1_js_render_pending"
    js_render_results: str = "stage1_js_render_results"
    stage1_errors: str = "stage1_error_queue"
    stage2_errors: str = "stage2_error_queue"
    stage3_errors: str = "stage3_error_queue"
    stage4_errors: str = "stage4_error_queue"

class AppConfig(BaseModel):

    environment: Environment = Environment.DEVELOPMENT

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    delta_lake: DeltaLakeConfig = Field(default_factory=DeltaLakeConfig)

    stage1: Stage1Config = Field(default_factory=Stage1Config)
    stage2: Stage2Config = Field(default_factory=Stage2Config)
    stage3: Stage3Config = Field(default_factory=Stage3Config)
    stage4: Stage4Config = Field(default_factory=Stage4Config)

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    message_queues: MessageQueuesConfig = Field(default_factory=MessageQueuesConfig)

    model_config = ConfigDict(arbitrary_types_allowed=True)

# ============================================================================
# ============================================================================

class ConfigManager:

    _instance: Optional["ConfigManager"] = None
    _config: AppConfig | None = None

    def __init__(self, config_path: Path | None = None, env_override: Environment | None = None):
        self._config_path = config_path
        self._env_override = env_override
        self._load_config()

    @classmethod
    def get_instance(cls, reset: bool = False) -> "ConfigManager":
        if cls._instance is None or reset:
            cls._instance = cls()
        return cls._instance

    def _load_config(self) -> None:
        raw_env = self._env_override or os.getenv("ENV", Environment.DEVELOPMENT.value)
        environment = raw_env if isinstance(raw_env, Environment) else Environment(str(raw_env).lower())

        yaml_config = self._load_yaml_config(environment)

        config_dict = self._apply_env_overrides(yaml_config)

        config_dict["environment"] = environment

        self._config = AppConfig(**config_dict)

    def _load_yaml_config(self, environment: Environment) -> dict[str, Any]:
        if self._config_path:
            config_path = self._config_path
        else:
            project_root = Path(__file__).parent.parent.parent

            env_config = project_root / "config" / f"{environment.value}.yml"
            if env_config.exists():
                config_path = env_config
            else:
                config_path = project_root / "config.yml"

        if not config_path.exists():
            print(f"Warning: Config file not found at {config_path}, using defaults")
            return {}

        with open(config_path) as f:
            return yaml.safe_load(f) or {}

    def _apply_env_overrides(self, yaml_config: dict[str, Any]) -> dict[str, Any]:
        config = yaml_config.copy()

        db_config = config.get("database", {}) if "database" in config else config.get("postgres", {})
        db_config["host"] = os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or db_config.get("host", "localhost")
        db_config["port"] = int(os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or db_config.get("port", 5432))
        db_config["database"] = (
            os.getenv("DB_NAME") or os.getenv("POSTGRES_DB") or db_config.get("database", "scraping_db")
        )
        db_config["user"] = os.getenv("DB_USER") or os.getenv("POSTGRES_USER") or db_config.get("user", "postgres")
        db_config["password"] = os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or db_config.get("password")
        config["database"] = db_config

        redis_config = config.get("redis", {})
        redis_config["url"] = os.getenv("REDIS_URL") or redis_config.get("url")
        redis_config["host"] = os.getenv("REDIS_HOST") or redis_config.get("host", "localhost")
        redis_config["port"] = int(os.getenv("REDIS_PORT") or redis_config.get("port", 6379))
        redis_config["password"] = os.getenv("REDIS_PASSWORD") or redis_config.get("password")
        config["redis"] = redis_config

        kafka_config = config.get("kafka", {})
        kafka_config["bootstrap_servers"] = os.getenv("KAFKA_BOOTSTRAP_SERVERS") or kafka_config.get(
            "bootstrap_servers", "localhost:9092"
        )
        kafka_config["topic"] = os.getenv("KAFKA_TOPIC") or kafka_config.get("topic", "scraped-items")

        kafka_config["security_protocol"] = os.getenv("KAFKA_SECURITY_PROTOCOL") or kafka_config.get(
            "security_protocol"
        )
        kafka_config["sasl_mechanism"] = os.getenv("KAFKA_SASL_MECHANISM") or kafka_config.get("sasl_mechanism")
        kafka_config["sasl_username"] = os.getenv("KAFKA_SASL_USERNAME") or kafka_config.get("sasl_username")
        kafka_config["sasl_password"] = os.getenv("KAFKA_SASL_PASSWORD") or kafka_config.get("sasl_password")
        config["kafka"] = kafka_config

        logging_config = config.get("logging", {})
        logging_config["level"] = os.getenv("LOG_LEVEL") or logging_config.get("level", "INFO")
        config["logging"] = logging_config

        monitoring_config = config.get("monitoring", {})
        monitoring_config["metrics_port"] = int(
            os.getenv("METRICS_PORT") or monitoring_config.get("metrics_port", 8000)
        )
        monitoring_config["metrics_interval"] = int(
            os.getenv("METRICS_INTERVAL") or monitoring_config.get("metrics_interval", 60)
        )
        config["monitoring"] = monitoring_config

        return config

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            raise RuntimeError("Configuration not loaded")
        return self._config

    @property
    def database(self) -> DatabaseConfig:
        return self.config.database

    @property
    def redis(self) -> RedisConfig:
        return self.config.redis

    @property
    def kafka(self) -> KafkaConfig:
        return self.config.kafka

    @property
    def delta_lake(self) -> DeltaLakeConfig:
        return self.config.delta_lake

    @property
    def stage1(self) -> Stage1Config:
        return self.config.stage1

    @property
    def stage2(self) -> Stage2Config:
        return self.config.stage2

    @property
    def stage3(self) -> Stage3Config:
        return self.config.stage3

    @property
    def stage4(self) -> Stage4Config:
        return self.config.stage4

    @property
    def logging(self) -> LoggingConfig:
        return self.config.logging

    @property
    def monitoring(self) -> MonitoringConfig:
        return self.config.monitoring

    @property
    def export(self) -> ExportConfig:
        return self.config.export

    @property
    def message_queues(self) -> MessageQueuesConfig:
        return self.config.message_queues

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        value = self.config.model_dump(mode="python")

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default

        return value

    def reload(self) -> None:
        self._load_config()

    def to_dict(self) -> dict[str, Any]:
        return self.config.model_dump(mode="python")

    def to_yaml(self, file_path: Path | None = None) -> str:
        config_dict = self.to_dict()

        def convert_enums(obj):
            if isinstance(obj, dict):
                return {k: convert_enums(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_enums(item) for item in obj]
            elif isinstance(obj, Enum):
                return obj.value
            else:
                return obj

        config_dict = convert_enums(config_dict)
        yaml_str = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

        if file_path:
            with open(file_path, "w") as f:
                f.write(yaml_str)

        return yaml_str

# ============================================================================
# ============================================================================

class Config:

    @staticmethod
    def get_instance():
        import warnings

        warnings.warn(
            "Config.get_instance() is deprecated. Use ConfigManager.get_instance() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ConfigManager.get_instance()

# ============================================================================
# ============================================================================

def get_config() -> ConfigManager:
    return ConfigManager.get_instance()

def get_database_config() -> DatabaseConfig:
    return get_config().database

def get_redis_config() -> RedisConfig:
    return get_config().redis

def get_kafka_config() -> KafkaConfig:
    return get_config().kafka
