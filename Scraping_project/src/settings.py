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

import yaml

# Determine environment and load YAML config
ENV = os.getenv('ENV', 'development')
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config' / f'{ENV}.yml'

# Load YAML configuration
_config = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            _config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logging.error(f"Error parsing YAML file: {e}")
# Extract Scrapy-specific settings from YAML
_scrapy_config = _config.get('scrapy', {})

BOT_NAME = _scrapy_config.get('bot_name', 'uconn_scraper')

SPIDER_MODULES = _scrapy_config.get('spider_modules', ['src.stage1', 'src.stage3'])
NEWSPIDER_MODULE = _scrapy_config.get('newspider_module', 'src.stage3')

# Obey robots.txt rules (from YAML or default)
ROBOTSTXT_OBEY = _scrapy_config.get('robotstxt_obey', False)

# Configure pipelines - Order matters! Lower numbers execute first
ITEM_PIPELINES = _scrapy_config.get('item_pipelines', {
    'src.pipelines.DataValidationPipeline': 100,   # First: Validate and drop invalid items
    'src.pipelines.DataCleansingPipeline': 200,    # Second: Cleanse and normalize data
    'src.pipelines.MetadataPipeline': 250,          # Third: Add operational metadata
    'src.pipelines.KafkaPipeline': 300,             # Fourth: Serialize and publish to Kafka
    'src.pipelines.OffsiteCandidatePipeline': 800,  # Fifth: Process and save offsite candidates
    'src.pipelines.GrafanaSummaryPipeline': 900,    # Sixth: Generate content summaries for Grafana
})

# Configure request fingerprinting (from YAML or default)
REQUEST_FINGERPRINTER_CLASS = _scrapy_config.get('request_fingerprinter_class', 'scrapy.utils.request.RequestFingerprinter')

# User agent (from YAML or default)
USER_AGENT = _scrapy_config.get('user_agent', 'UConn-Discovery-Crawler/1.0')

# Concurrency settings (from YAML)
CONCURRENT_REQUESTS = _scrapy_config.get('concurrent_requests', 64)
CONCURRENT_REQUESTS_PER_DOMAIN = _scrapy_config.get('concurrent_requests_per_domain', 32)
CONCURRENT_REQUESTS_PER_IP = _scrapy_config.get('concurrent_requests_per_ip', 32)

# Download settings (from YAML)
DOWNLOAD_DELAY = _scrapy_config.get('download_delay', 0.1)
DOWNLOAD_TIMEOUT = _scrapy_config.get('download_timeout', 10)
DNS_TIMEOUT = _scrapy_config.get('dns_timeout', 5)

# Retry settings (from YAML)
RETRY_ENABLED = _scrapy_config.get('retry_enabled', True)
RETRY_TIMES = _scrapy_config.get('retry_times', 2)

# Log level (from YAML)
LOG_LEVEL = _scrapy_config.get('log_level', 'INFO')

# ============================================================================
# Spider Close Settings (K2: Safety hotfix)
# ============================================================================
# Global timeout for spider execution (dev: 5-10 min, prod: longer)
# Ensures graceful shutdown and prevents infinite crawling
CLOSESPIDER_TIMEOUT = _scrapy_config.get('closespider_timeout', 600)  # 10 minutes default for dev

# Enable and configure the AutoThrottle extension (disabled by default)
# AutoThrottle dynamically adjusts concurrency based on server response times
AUTOTHROTTLE_ENABLED = _scrapy_config.get('autothrottle_enabled', True)
AUTOTHROTTLE_START_DELAY = _scrapy_config.get('autothrottle_start_delay', 0.1)
AUTOTHROTTLE_MAX_DELAY = _scrapy_config.get('autothrottle_max_delay', 1.0)
AUTOTHROTTLE_TARGET_CONCURRENCY = float(CONCURRENT_REQUESTS)
AUTOTHROTTLE_DEBUG = _scrapy_config.get('autothrottle_debug', False)

# Enable and configure HTTP caching (using DBM storage instead of pickle for security)
HTTPCACHE_ENABLED = _scrapy_config.get('httpcache_enabled', True)
HTTPCACHE_EXPIRATION_SECS = _scrapy_config.get('httpcache_expiration_secs', 3600)
HTTPCACHE_DIR = PROJECT_ROOT / 'data' / 'cache' / 'scrapy'
HTTPCACHE_STORAGE = _scrapy_config.get('httpcache_storage', 'scrapy.extensions.httpcache.DbmCacheStorage')

TWISTED_REACTOR = _scrapy_config.get('twisted_reactor', 'twisted.internet.asyncioreactor.AsyncioSelectorReactor')
FEED_EXPORT_ENCODING = _scrapy_config.get('feed_export_encoding', 'utf-8')

# Playwright settings
# DOWNLOAD_HANDLERS is disabled by default to avoid conflicts with other extensions.
# To enable Playwright, uncomment the following lines and ensure scrapy-playwright is installed.
# DOWNLOAD_HANDLERS = _scrapy_config.get('download_handlers', {})
PLAYWRIGHT_BROWSER_TYPE = _scrapy_config.get('playwright_browser_type', 'chromium')
PLAYWRIGHT_LAUNCH_OPTIONS = _scrapy_config.get('playwright_launch_options', {'headless': True})

# ============================================================================
# Extensions Configuration
# ============================================================================
# Enable custom extensions (order matters - lower numbers are loaded first)
EXTENSIONS = _scrapy_config.get('extensions', {
    'src.scrapy_prometheus.PrometheusExtension': 500,
})

# ============================================================================
# Prometheus Metrics Configuration
# ============================================================================
# Enable Prometheus metrics exporter
PROMETHEUS_ENABLED = _scrapy_config.get('prometheus_enabled', True)

# Metrics endpoint configuration
PROMETHEUS_PORT = _scrapy_config.get('prometheus_port', 9410)
PROMETHEUS_HOST = _scrapy_config.get('prometheus_host', '0.0.0.0')  # Listen on all interfaces
PROMETHEUS_PATH = _scrapy_config.get('prometheus_path', 'metrics')

# ============================================================================
# Kafka Configuration
# ============================================================================
# Kafka pipeline for real-time event streaming of scraped items
# Security best practice: Never hardcode credentials! Load from environment variables.

# Required: Kafka broker addresses (comma-separated host:port pairs)
KAFKA_BOOTSTRAP_SERVERS = _scrapy_config.get(
    'kafka_bootstrap_servers',
    os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
)

# Required: Target topic for scraped items
KAFKA_TOPIC = _scrapy_config.get(
    'kafka_topic',
    os.getenv('KAFKA_TOPIC', 'scraped-items')
)

# Optional: Additional producer configuration
# For production, consider tuning these parameters:
# - acks: 'all' for maximum durability (trades off throughput)
# - compression.type: 'lz4' or 'zstd' for better compression (CPU vs bandwidth tradeoff)
# - max.in.flight.requests.per.connection: 1 for strict ordering (trades off throughput)
KAFKA_PRODUCER_CONFIG = _scrapy_config.get('kafka_producer_config', {})

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
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff',
    # Stylesheets and scripts
    '.css', '.js', '.map',
    # Archives
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
    # Documents (queued for later processing, not crawled)
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    # Media files
    '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4a', '.wav',
    # Fonts
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    # Other binary formats
    '.exe', '.dmg', '.pkg', '.deb', '.rpm',
]

# Batch size for Delta Lake writes (number of records before writing)
DELTA_BATCH_SIZE = _scrapy_config.get('delta_batch_size', 50)
