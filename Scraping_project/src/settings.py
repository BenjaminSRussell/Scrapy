import logging
import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ENV = os.getenv("ENV", "development")
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / f"{ENV}.yml"

_config: dict[str, Any] = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logging.error(f"Error parsing YAML file: {e}")
_scrapy_config: dict[str, Any] = _config.get("scrapy", {})

BOT_NAME = _scrapy_config.get("bot_name", "uconn_scraper")

SPIDER_MODULES = _scrapy_config.get("spider_modules", ["src.stage1", "src.stage3"])
NEWSPIDER_MODULE = _scrapy_config.get("newspider_module", "src.stage3")

ITEM_PIPELINES = _scrapy_config.get(
    "item_pipelines",
    {
        "src.pipelines.DataValidationPipeline": 100,
        "src.pipelines.DataCleansingPipeline": 150,
        "src.pipelines.SchemaValidationPipeline": 200,
        "src.pipelines.MetadataPipeline": 250,
        "src.pipelines.RecencyScoringPipeline": 300,
        "src.pipelines.KafkaPipeline": 400,
        "src.pipelines.AggregationPipeline": 500,
        "src.pipelines.OffsiteCandidatePipeline": 800,
        "src.pipelines.GrafanaSummaryPipeline": 900,
    },
)

REQUEST_FINGERPRINTER_CLASS = _scrapy_config.get(
    "request_fingerprinter_class", "scrapy.utils.request.RequestFingerprinter"
)

USER_AGENT = _scrapy_config.get("user_agent", "UConn-Discovery-Crawler/1.0")

CONCURRENT_REQUESTS = _scrapy_config.get("concurrent_requests", 64)
CONCURRENT_REQUESTS_PER_DOMAIN = _scrapy_config.get("concurrent_requests_per_domain", 32)
CONCURRENT_REQUESTS_PER_IP = _scrapy_config.get("concurrent_requests_per_ip", 32)

DOWNLOAD_DELAY = _scrapy_config.get("download_delay", 0.1)
DOWNLOAD_TIMEOUT = _scrapy_config.get("download_timeout", 10)
DNS_TIMEOUT = _scrapy_config.get("dns_timeout", 5)

RETRY_ENABLED = _scrapy_config.get("retry_enabled", True)
RETRY_TIMES = _scrapy_config.get("retry_times", 2)

LOG_LEVEL = _scrapy_config.get("log_level", "INFO")

# ============================================================================
# ============================================================================
CLOSESPIDER_TIMEOUT = _scrapy_config.get("closespider_timeout", 600)

AUTOTHROTTLE_ENABLED = _scrapy_config.get("autothrottle_enabled", True)
AUTOTHROTTLE_START_DELAY = _scrapy_config.get("autothrottle_start_delay", 0.1)
AUTOTHROTTLE_MAX_DELAY = _scrapy_config.get("autothrottle_max_delay", 1.0)
AUTOTHROTTLE_TARGET_CONCURRENCY = float(CONCURRENT_REQUESTS)
AUTOTHROTTLE_DEBUG = _scrapy_config.get("autothrottle_debug", False)

HTTPCACHE_ENABLED = _scrapy_config.get("httpcache_enabled", True)
HTTPCACHE_EXPIRATION_SECS = _scrapy_config.get("httpcache_expiration_secs", 3600)
HTTPCACHE_DIR = PROJECT_ROOT / "data" / "cache" / "scrapy"
HTTPCACHE_STORAGE = _scrapy_config.get("httpcache_storage", "scrapy.extensions.httpcache.DbmCacheStorage")

TWISTED_REACTOR = _scrapy_config.get("twisted_reactor", "twisted.internet.asyncioreactor.AsyncioSelectorReactor")
FEED_EXPORT_ENCODING = _scrapy_config.get("feed_export_encoding", "utf-8")

# ============================================================================
# ============================================================================
DUPEFILTER_CLASS = "scrapy.dupefilters.BaseDupeFilter"

COOKIES_ENABLED = False
DEPTH_LIMIT = 10
DEPTH_PRIORITY = 1
DEPTH_STATS_VERBOSE = True

PLAYWRIGHT_BROWSER_TYPE = _scrapy_config.get("playwright_browser_type", "chromium")
PLAYWRIGHT_LAUNCH_OPTIONS = _scrapy_config.get("playwright_launch_options", {"headless": True})

# ============================================================================
# ============================================================================
EXTENSIONS = _scrapy_config.get(
    "extensions",
    {
        "src.scrapy_prometheus.PrometheusExtension": 500,
    },
)

# ============================================================================
# ============================================================================
PROMETHEUS_ENABLED = _scrapy_config.get("prometheus_enabled", True)

PROMETHEUS_PORT = _scrapy_config.get("prometheus_port", 9410)
PROMETHEUS_HOST = _scrapy_config.get("prometheus_host", "0.0.0.0")
PROMETHEUS_PATH = _scrapy_config.get("prometheus_path", "metrics")

# ============================================================================
# ============================================================================

KAFKA_BOOTSTRAP_SERVERS = _scrapy_config.get(
    "kafka_bootstrap_servers", os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
)

KAFKA_TOPIC = _scrapy_config.get("kafka_topic", os.getenv("KAFKA_TOPIC", "scraped-items"))

KAFKA_PRODUCER_CONFIG = _scrapy_config.get("kafka_producer_config", {})

# ============================================================================
# ============================================================================

IGNORED_EXTENSIONS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    ".ico",
    ".tiff",
    ".css",
    ".js",
    ".map",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bz2",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4a",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".exe",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
]

DELTA_BATCH_SIZE = _scrapy_config.get("delta_batch_size", 50)

# ============================================================================
# ============================================================================
SCHEMA_VALIDATION_ENABLED = _scrapy_config.get("schema_validation_enabled", True)

VALIDATION_FAILURES_TOPIC = _scrapy_config.get("validation_failures_topic", "validation_failures")

# ============================================================================
# ============================================================================
RECENCY_DECAY_CONSTANT = _scrapy_config.get("recency_decay_constant", 0.01)

RECENCY_DEFAULT_SCORE = _scrapy_config.get("recency_default_score", 0.5)

# ============================================================================
# ============================================================================
AGGREGATION_ENABLED = _scrapy_config.get("aggregation_enabled", True)

AGGREGATION_OUTPUT_TOPIC = _scrapy_config.get("aggregation_output_topic", "entity_summaries")

# ============================================================================
# ============================================================================

ZSC_INPUT_TOPIC = _scrapy_config.get("zsc_input_topic", "validated_items")

ZSC_OUTPUT_TOPIC = _scrapy_config.get("zsc_output_topic", "final_categorized")

ZSC_LOW_CONF_TOPIC = _scrapy_config.get("zsc_low_conf_topic", "low_confidence_review")

ZSC_CONFIDENCE_THRESHOLD = _scrapy_config.get("zsc_confidence_threshold", 0.85)

ZSC_MODEL_NAME = _scrapy_config.get("zsc_model_name", "facebook/bart-large-mnli")

ZSC_DEVICE = _scrapy_config.get("zsc_device", -1)

# ============================================================================
# ============================================================================
ASR_MAX_WORKERS = _scrapy_config.get("asr_max_workers", 4)

ASR_ENABLED = _scrapy_config.get("asr_enabled", False)

# ============================================================================
# ============================================================================
KAFKA_TOPIC = _scrapy_config.get("kafka_topic", os.getenv("KAFKA_TOPIC", "validated_items"))

# Note: The system uses multiple Kafka topics for architectural decoupling:
