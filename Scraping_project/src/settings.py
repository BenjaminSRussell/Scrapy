"""Scrapy settings for uconn_scraper project

This file contains base Scrapy settings. Runtime-configurable settings are loaded
from config/development.yml or config/production.yml (see src/orchestrator/config.py)

Configuration precedence (highest to lowest):
1. Command-line arguments passed to Scrapy
2. Settings in this file
3. YAML config files (config/development.yml or config/production.yml)
4. Scrapy defaults

For more info: https://docs.scrapy.org/en/latest/topics/settings.html
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

# Determine environment and load YAML config
ENV = os.getenv("ENV", "development")
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / f"{ENV}.yml"

# Load YAML configuration
_config: dict[str, Any] = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logging.error(f"Error parsing YAML file: {e}")
# Extract Scrapy-specific settings from YAML
_scrapy_config: dict[str, Any] = _config.get("scrapy", {})

BOT_NAME = _scrapy_config.get("bot_name", "uconn_scraper")

SPIDER_MODULES = _scrapy_config.get("spider_modules", ["src.stage1", "src.stage3"])
NEWSPIDER_MODULE = _scrapy_config.get("newspider_module", "src.stage3")

# Configure pipelines - Order matters! Lower numbers execute first
ITEM_PIPELINES = _scrapy_config.get(
    "item_pipelines",
    {
        # Part 1: High-Integrity Data Ingestion
        "src.pipelines.DataValidationPipeline": 100,  # Basic field validation
        "src.pipelines.DataCleansingPipeline": 150,  # Normalize and cleanse data
        "src.pipelines.SchemaValidationPipeline": 200,  # Pydantic schema validation with type coercion
        "src.pipelines.MetadataPipeline": 250,  # Add operational metadata
        "src.pipelines.RecencyScoringPipeline": 300,  # Calculate recency scores for temporal relevance
        # Part 4: Kafka Decoupling
        "src.pipelines.KafkaPipeline": 400,  # Publish to validated_items topic for ZSC processing
        # Aggregation (happens in-memory, doesn't block)
        "src.pipelines.AggregationPipeline": 500,  # Entity grouping and summarization
        # Special purpose pipelines
        "src.pipelines.OffsiteCandidatePipeline": 800,  # Process offsite candidates
        "src.pipelines.GrafanaSummaryPipeline": 900,  # Generate content summaries for Grafana
    },
)

# Configure request fingerprinting (from YAML or default)
REQUEST_FINGERPRINTER_CLASS = _scrapy_config.get(
    "request_fingerprinter_class", "scrapy.utils.request.RequestFingerprinter"
)

# User agent (from YAML or default)
USER_AGENT = _scrapy_config.get("user_agent", "UConn-Discovery-Crawler/1.0")

# Concurrency settings (from YAML)
CONCURRENT_REQUESTS = _scrapy_config.get("concurrent_requests", 64)
CONCURRENT_REQUESTS_PER_DOMAIN = _scrapy_config.get("concurrent_requests_per_domain", 32)
CONCURRENT_REQUESTS_PER_IP = _scrapy_config.get("concurrent_requests_per_ip", 32)

# Download settings (from YAML)
DOWNLOAD_DELAY = _scrapy_config.get("download_delay", 0.1)
DOWNLOAD_TIMEOUT = _scrapy_config.get("download_timeout", 10)
DNS_TIMEOUT = _scrapy_config.get("dns_timeout", 5)

# Retry settings (from YAML)
RETRY_ENABLED = _scrapy_config.get("retry_enabled", True)
RETRY_TIMES = _scrapy_config.get("retry_times", 2)

# Log level (from YAML)
LOG_LEVEL = _scrapy_config.get("log_level", "INFO")

# ============================================================================
# Spider Close Settings (K2: Safety hotfix)
# ============================================================================
# Global timeout for spider execution (dev: 5-10 min, prod: longer)
# Ensures graceful shutdown and prevents infinite crawling
CLOSESPIDER_TIMEOUT = _scrapy_config.get("closespider_timeout", 600)  # 10 minutes default for dev

# Enable and configure the AutoThrottle extension (disabled by default)
# AutoThrottle dynamically adjusts concurrency based on server response times
AUTOTHROTTLE_ENABLED = _scrapy_config.get("autothrottle_enabled", True)
AUTOTHROTTLE_START_DELAY = _scrapy_config.get("autothrottle_start_delay", 0.1)
AUTOTHROTTLE_MAX_DELAY = _scrapy_config.get("autothrottle_max_delay", 1.0)
AUTOTHROTTLE_TARGET_CONCURRENCY = float(CONCURRENT_REQUESTS)
AUTOTHROTTLE_DEBUG = _scrapy_config.get("autothrottle_debug", False)

# Enable and configure HTTP caching (using DBM storage instead of pickle for security)
HTTPCACHE_ENABLED = _scrapy_config.get("httpcache_enabled", True)
HTTPCACHE_EXPIRATION_SECS = _scrapy_config.get("httpcache_expiration_secs", 3600)
HTTPCACHE_DIR = PROJECT_ROOT / "data" / "cache" / "scrapy"
HTTPCACHE_STORAGE = _scrapy_config.get("httpcache_storage", "scrapy.extensions.httpcache.DbmCacheStorage")

TWISTED_REACTOR = _scrapy_config.get("twisted_reactor", "twisted.internet.asyncioreactor.AsyncioSelectorReactor")
FEED_EXPORT_ENCODING = _scrapy_config.get("feed_export_encoding", "utf-8")

# ============================================================================
# Dupefilter Configuration - COMPLETELY DISABLED for aggressive re-scraping
# ============================================================================
# Disable dupefilter completely to allow re-scraping ALL URLs
# This enables seed URL expansion and continuous crawling
DUPEFILTER_CLASS = "scrapy.dupefilters.BaseDupeFilter"
# Alternative: Use RFPDupeFilter (respects dont_filter=True in requests)
# DUPEFILTER_CLASS = "scrapy.dupefilters.RFPDupeFilter"

# Additional settings for depth and cookies
COOKIES_ENABLED = False  # Disable cookies for faster crawling
DEPTH_LIMIT = 10  # Maximum crawl depth (can be overridden per spider)
DEPTH_PRIORITY = 1  # Prioritize shallower pages
DEPTH_STATS_VERBOSE = True  # Log detailed depth statistics

# Playwright settings
# DOWNLOAD_HANDLERS is disabled by default to avoid conflicts with other extensions.
# To enable Playwright, uncomment the following lines and ensure scrapy-playwright is installed.
# DOWNLOAD_HANDLERS = _scrapy_config.get('download_handlers', {})
PLAYWRIGHT_BROWSER_TYPE = _scrapy_config.get("playwright_browser_type", "chromium")
PLAYWRIGHT_LAUNCH_OPTIONS = _scrapy_config.get("playwright_launch_options", {"headless": True})

# ============================================================================
# Extensions Configuration
# ============================================================================
# Enable custom extensions (order matters - lower numbers are loaded first)
EXTENSIONS = _scrapy_config.get(
    "extensions",
    {
        "src.scrapy_prometheus.PrometheusExtension": 500,
    },
)

# ============================================================================
# Prometheus Metrics Configuration
# ============================================================================
# Enable Prometheus metrics exporter
PROMETHEUS_ENABLED = _scrapy_config.get("prometheus_enabled", True)

# Metrics endpoint configuration
PROMETHEUS_PORT = _scrapy_config.get("prometheus_port", 9410)
PROMETHEUS_HOST = _scrapy_config.get("prometheus_host", "0.0.0.0")  # Listen on all interfaces
PROMETHEUS_PATH = _scrapy_config.get("prometheus_path", "metrics")

# ============================================================================
# Kafka Configuration
# ============================================================================
# Kafka pipeline for real-time event streaming of scraped items
# Security best practice: Never hardcode credentials! Load from environment variables.

# Required: Kafka broker addresses (comma-separated host:port pairs)
KAFKA_BOOTSTRAP_SERVERS = _scrapy_config.get(
    "kafka_bootstrap_servers", os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
)

# Required: Target topic for scraped items
KAFKA_TOPIC = _scrapy_config.get("kafka_topic", os.getenv("KAFKA_TOPIC", "scraped-items"))

# Optional: Additional producer configuration
# For production, consider tuning these parameters:
# - acks: 'all' for maximum durability (trades off throughput)
# - compression.type: 'lz4' or 'zstd' for better compression (CPU vs bandwidth tradeoff)
# - max.in.flight.requests.per.connection: 1 for strict ordering (trades off throughput)
KAFKA_PRODUCER_CONFIG = _scrapy_config.get("kafka_producer_config", {})

# Security credentials (MUST be loaded from environment variables, never hardcode!)
# Supported authentication mechanisms:
# - SASL/PLAIN: Username/password authentication
# - SASL/SCRAM-SHA-256: More secure hashed authentication
# - SASL/SCRAM-SHA-512: Even more secure hashed authentication
# - SSL: Certificate-based authentication
#
# Example environment variables:
# export KAFKA_SECURITY_PROTOCOL='SASL_SSL'
# export KAFKA_SASL_MECHANISM='SCRAM-SHA-256'
# export KAFKA_SASL_USERNAME='your-username'
# export KAFKA_SASL_PASSWORD='your-password'
#
# The KafkaPipeline will automatically load these from os.getenv() at runtime.

# ============================================================================
# Spider Configuration - Centralized Settings
# ============================================================================

# File extensions to ignore during crawling (single source of truth)
# Spiders will skip following links to these file types
IGNORED_EXTENSIONS = [
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    ".ico",
    ".tiff",
    # Stylesheets and scripts
    ".css",
    ".js",
    ".map",
    # Archives
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bz2",
    # Documents (queued for later processing, not crawled)
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    # Media files
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4a",
    ".wav",
    # Fonts
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    # Other binary formats
    ".exe",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
]

# Batch size for Delta Lake writes (number of records before writing)
DELTA_BATCH_SIZE = _scrapy_config.get("delta_batch_size", 50)

# ============================================================================
# Part 1: Schema Validation Configuration
# ============================================================================
# Enable Pydantic-based schema validation with type coercion
SCHEMA_VALIDATION_ENABLED = _scrapy_config.get("schema_validation_enabled", True)

# Kafka topic for publishing validation failures
VALIDATION_FAILURES_TOPIC = _scrapy_config.get("validation_failures_topic", "validation_failures")

# ============================================================================
# Part 3: Recency Scoring Configuration
# ============================================================================
# Exponential decay constant (k) for recency scoring
# Higher values = faster decay. Default 0.01 means ~63% score after 100 days
RECENCY_DECAY_CONSTANT = _scrapy_config.get("recency_decay_constant", 0.01)

# Default recency score for items without publication_date
RECENCY_DEFAULT_SCORE = _scrapy_config.get("recency_default_score", 0.5)

# ============================================================================
# Part 3: Aggregation Pipeline Configuration
# ============================================================================
# Enable entity aggregation and LLM summarization
AGGREGATION_ENABLED = _scrapy_config.get("aggregation_enabled", True)

# Kafka topic for publishing entity summaries
AGGREGATION_OUTPUT_TOPIC = _scrapy_config.get("aggregation_output_topic", "entity_summaries")

# ============================================================================
# Part 2: Zero-Shot Classification Configuration
# ============================================================================
# ZSC Microservice settings (run separately: python -m src.ml_service)
# These are loaded from environment variables in the microservice

# Input topic for ZSC (should match KAFKA_TOPIC from KafkaPipeline)
ZSC_INPUT_TOPIC = _scrapy_config.get("zsc_input_topic", "validated_items")

# Output topic for high-confidence classifications
ZSC_OUTPUT_TOPIC = _scrapy_config.get("zsc_output_topic", "final_categorized")

# Output topic for low-confidence items requiring human review
ZSC_LOW_CONF_TOPIC = _scrapy_config.get("zsc_low_conf_topic", "low_confidence_review")

# Minimum confidence threshold for ZSC (0.0 - 1.0)
ZSC_CONFIDENCE_THRESHOLD = _scrapy_config.get("zsc_confidence_threshold", 0.85)

# Pre-trained NLI model for zero-shot classification
ZSC_MODEL_NAME = _scrapy_config.get("zsc_model_name", "facebook/bart-large-mnli")

# Device for ZSC inference (-1 = CPU, 0 = GPU:0, etc.)
ZSC_DEVICE = _scrapy_config.get("zsc_device", -1)

# ============================================================================
# Part 4: Async ASR Processing Configuration
# ============================================================================
# Maximum number of parallel ASR transcription processes
ASR_MAX_WORKERS = _scrapy_config.get("asr_max_workers", 4)

# Enable ASR middleware (set to False to disable automatic transcription)
ASR_ENABLED = _scrapy_config.get("asr_enabled", False)

# ============================================================================
# Updated Kafka Configuration for Multi-Topic Architecture
# ============================================================================
# Primary Kafka topic (validated_items) - consumed by ZSC microservice
KAFKA_TOPIC = _scrapy_config.get("kafka_topic", os.getenv("KAFKA_TOPIC", "validated_items"))

# Note: The system uses multiple Kafka topics for architectural decoupling:
# - validation_failures: Items that failed schema validation
# - validated_items: Items that passed validation (consumed by ZSC)
# - final_categorized: High-confidence categorized items from ZSC
# - low_confidence_review: Low-confidence items for human auditing
# - entity_summaries: Aggregated entity summaries from LLM
