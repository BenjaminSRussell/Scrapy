# Scrapy settings for uconn_scraper project
#
# This file contains base Scrapy settings. Runtime-configurable settings are loaded
# from config/development.yml or config/production.yml (see src/orchestrator/config.py)
#
# Configuration precedence (highest to lowest):
# 1. Command-line arguments passed to Scrapy
# 2. Settings in this file
# 3. YAML config files (config/development.yml or config/production.yml)
# 4. Scrapy defaults
#
# For more info: https://docs.scrapy.org/en/latest/topics/settings.html

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
    with open(CONFIG_PATH) as f:
        _config = yaml.safe_load(f) or {}

# Extract Scrapy-specific settings from YAML
_scrapy_config = _config.get('scrapy', {})

BOT_NAME = 'uconn_scraper'

SPIDER_MODULES = ['src.stage1', 'src.stage3']
NEWSPIDER_MODULE = 'src.stage3'

# Obey robots.txt rules (from YAML or default)
ROBOTSTXT_OBEY = _scrapy_config.get('robotstxt_obey', False)

# Configure pipelines
ITEM_PIPELINES = {
    'src.stage3.enrichment_pipeline.Stage3Pipeline': 300,
}

# Configure request fingerprinting (from YAML or default)
REQUEST_FINGERPRINTER_IMPLEMENTATION = _scrapy_config.get('request_fingerprinter_implementation', '2.7')

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

# Enable and configure the AutoThrottle extension (disabled by default)
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.1
AUTOTHROTTLE_MAX_DELAY = 1.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 16.0
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (using DBM storage instead of pickle for security)
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 3600
HTTPCACHE_DIR = 'data/cache/scrapy'
HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.DbmCacheStorage'

TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'
FEED_EXPORT_ENCODING = 'utf-8'

# Playwright settings (DISABLED - scrapy_playwright not installed)
# To enable: install scrapy_playwright and uncomment the lines below
# DOWNLOAD_HANDLERS = {
#     "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
#     "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
# }
# PLAYWRIGHT_BROWSER_TYPE = "chromium"
# PLAYWRIGHT_LAUNCH_OPTIONS = {
#     "headless": True,
# }
