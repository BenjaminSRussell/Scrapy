"""Scrapy project settings.

Canonical configuration lives in Scraping_project/config.yml and is loaded via
``src.core.config.get_config()``. Scrapy settings are derived from that SSOT
(with optional ``scrapy:`` overrides and env vars). Do not rely on
``config/{ENV}.yml`` for normal operation — that path is unused.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from src.core.config import Config, get_config
from src.core.tls_policy import downloader_context_factory
from src.stage1.middlewares.fetch_policy_middleware import DEFAULT_LARGE_DOC_EXTENSIONS

ENV = os.getenv("ENV", "development")
PROJECT_ROOT = Path(__file__).parent.parent


def derive_scrapy_config(config: Optional[Config] = None) -> dict[str, Any]:
    """Build the Scrapy settings overlay from config.yml / get_config().

    Preference per key:
    1. Explicit ``scrapy.*`` section in config.yml
    2. Bridged values from other config.yml sections (kafka, logging, scout)
    3. Hard-coded defaults at the assignment sites below

    Env vars (e.g. KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, LOG_LEVEL) still win
    where applied at assignment time.
    """
    cfg = config if config is not None else get_config()
    scrapy: dict[str, Any] = dict(cfg.get_section("scrapy") or {})

    bridges: dict[str, Any] = {
        "kafka_bootstrap_servers": cfg.get("kafka.bootstrap_servers"),
        "kafka_topic": cfg.get("kafka.topics.scraped_items"),
        "validation_failures_topic": cfg.get("kafka.topics.validation_failures"),  # #410
        "kafka_producer_config": (cfg.get_section("kafka") or {}).get("producer"),
        "kafka_message_key_field": cfg.get("kafka.message_key_field"),
        "kafka_require_idempotence": cfg.get("kafka.require_idempotence"),
        "log_level": cfg.get("logging.level"),
        # Scout spider settings are the default Scrapy concurrency baseline
        "concurrent_requests": cfg.get("stage1.spiders.scout.concurrent_requests"),
        "concurrent_requests_per_domain": cfg.get(
            "stage1.spiders.scout.concurrent_requests_per_domain"
        ),
        "download_delay": cfg.get("stage1.spiders.scout.download_delay"),
        "download_timeout": cfg.get("stage1.spiders.scout.download_timeout"),
        "retry_times": cfg.get("stage1.spiders.scout.retry_times"),
        "dns_timeout": cfg.get("stage1.spiders.scout.dns_timeout"),
        "autothrottle_enabled": cfg.get("stage1.spiders.scout.autothrottle_enabled"),
        "autothrottle_start_delay": cfg.get(
            "stage1.spiders.scout.autothrottle_start_delay"
        ),
        "autothrottle_max_delay": cfg.get("stage1.spiders.scout.autothrottle_max_delay"),
        # #395/#396: stage1.fetch_policy (cookies, large-document timeouts)
        "cookies_enabled": cfg.get("stage1.fetch_policy.cookies_enabled"),
        "cookies_allowed_domains": cfg.get("stage1.fetch_policy.cookies_allowed_domains"),
        "large_doc_download_timeout": cfg.get("stage1.fetch_policy.large_doc_download_timeout"),
        "large_doc_maxsize": cfg.get("stage1.fetch_policy.large_doc_maxsize"),
        "large_doc_extensions": cfg.get("stage1.fetch_policy.large_doc_extensions"),
        "large_doc_url_patterns": cfg.get("stage1.fetch_policy.large_doc_url_patterns"),
    }
    for key, value in bridges.items():
        if key not in scrapy and value is not None:
            scrapy[key] = value
    return scrapy


_scrapy_config: dict[str, Any] = derive_scrapy_config()

BOT_NAME = _scrapy_config.get("bot_name", "uconn_scraper")

# src.stage3 holds the Stage3 worker, not spiders (#628).
SPIDER_MODULES = _scrapy_config.get("spider_modules", ["src.stage1"])
NEWSPIDER_MODULE = _scrapy_config.get("newspider_module", "src.stage1")

# SSRF guard (#682): refuse loopback/private/link-local/metadata/service-name
# targets before download, on every redirect hop. Registered in
# DOWNLOADER_MIDDLEWARES below (and by spider_config for spider custom_settings).
SSRF_GUARD_ENABLED = os.getenv("SSRF_GUARD_ENABLED", "1") != "0"
SSRF_RESOLVE_DNS = os.getenv("SSRF_RESOLVE_DNS", "0") == "1"
SSRF_ALLOWED_HOSTS = os.getenv("SSRF_ALLOWED_HOSTS", "")

ITEM_PIPELINES = _scrapy_config.get(
    "item_pipelines",
    {
        "src.otel_tracing.OtelItemPipeline": 50,
        "src.pipelines.DataValidationPipeline": 100,
        "src.pipelines.DataCleansingPipeline": 150,
        # Stage1 -> Stage2 / JS queue handoff (#608). Before SchemaValidation:
        # Scout's routing dicts are not content records and must not be dropped.
        "src.pipelines.QueueItemPipeline": 175,
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

# robots.txt (#186, #188): obey Disallow and Crawl-delay by default. Scrapy's own
# RobotsTxtMiddleware is swapped for PoliteRobotsTxtMiddleware (adds metrics and
# Crawl-delay, capped at ROBOTS_MAX_CRAWL_DELAY). Opt out only for sites you own:
# ROBOTSTXT_OBEY=false or scrapy.robotstxt_obey: false.
ROBOTSTXT_OBEY = str(os.getenv("ROBOTSTXT_OBEY", _scrapy_config.get("robotstxt_obey", True))).strip().lower() not in {
    "0", "false", "no", "off"
}
ROBOTS_MAX_CRAWL_DELAY = float(os.getenv("ROBOTS_MAX_CRAWL_DELAY", _scrapy_config.get("robots_max_crawl_delay", 60)))
DOWNLOADER_MIDDLEWARES = _scrapy_config.get(
    "downloader_middlewares",
    {
        "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
        "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware": 100,
        "src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware": 560,  # #188
    },
)
# #682: the SSRF guard runs first, even when config.yml overrides the dict
# (set it to None there to opt out explicitly; SSRF_GUARD_ENABLED=0 also works).
DOWNLOADER_MIDDLEWARES = dict(DOWNLOADER_MIDDLEWARES)
DOWNLOADER_MIDDLEWARES.setdefault("src.stage1.middlewares.ssrf_middleware.SSRFGuardMiddleware", 50)
RETRY_AFTER_MAX_DELAY = float(os.getenv("RETRY_AFTER_MAX_DELAY", _scrapy_config.get("retry_after_max_delay", 120)))

CONCURRENT_REQUESTS = _scrapy_config.get("concurrent_requests", 64)
CONCURRENT_REQUESTS_PER_DOMAIN = _scrapy_config.get("concurrent_requests_per_domain", 32)
CONCURRENT_REQUESTS_PER_IP = _scrapy_config.get("concurrent_requests_per_ip", 32)

# Verify TLS certificates (#584). Scrapy's default context factory accepts any
# certificate; see src/core/tls_policy.py for the gated override.
DOWNLOADER_CLIENTCONTEXTFACTORY = downloader_context_factory()

DOWNLOAD_DELAY = _scrapy_config.get("download_delay", 0.1)
DOWNLOAD_TIMEOUT = _scrapy_config.get("download_timeout", 10)
# #396: HTML keeps the short DOWNLOAD_TIMEOUT; PDF/Office/archive URLs get a
# longer timeout and size cap from FetchPolicyMiddleware (registered below).
LARGE_DOC_DOWNLOAD_TIMEOUT = float(_scrapy_config.get("large_doc_download_timeout", 120))
LARGE_DOC_MAXSIZE = int(_scrapy_config.get("large_doc_maxsize", 100 * 1024 * 1024))
LARGE_DOC_EXTENSIONS = _scrapy_config.get("large_doc_extensions", list(DEFAULT_LARGE_DOC_EXTENSIONS))
LARGE_DOC_URL_PATTERNS = _scrapy_config.get("large_doc_url_patterns", []) or []
DOWNLOADER_MIDDLEWARES = dict(DOWNLOADER_MIDDLEWARES)
# Before DownloadTimeoutMiddleware (350) and CookiesMiddleware (700).
DOWNLOADER_MIDDLEWARES.setdefault("src.stage1.middlewares.fetch_policy_middleware.FetchPolicyMiddleware", 340)
DNS_TIMEOUT = _scrapy_config.get("dns_timeout", 5)

RETRY_ENABLED = _scrapy_config.get("retry_enabled", True)
RETRY_TIMES = _scrapy_config.get("retry_times", 2)

LOG_LEVEL = os.getenv("LOG_LEVEL", _scrapy_config.get("log_level", "INFO"))

# ============================================================================
# ============================================================================
CLOSESPIDER_TIMEOUT = _scrapy_config.get("closespider_timeout", 600)

# Politeness (#194): AutoThrottle must be able to slow a host down far enough
# under a 429 storm. AUTOTHROTTLE_MAX_DELAY is floored at 30s and the target
# concurrency is per remote host (Scrapy semantics), so it can never exceed
# CONCURRENT_REQUESTS_PER_DOMAIN. See README "Rate limits and per-domain
# concurrency".
from src.stage1.middlewares.spider_config import (  # noqa: E402
    polite_autothrottle_max_delay,
    polite_target_concurrency,
)

AUTOTHROTTLE_ENABLED = _scrapy_config.get("autothrottle_enabled", True)
AUTOTHROTTLE_START_DELAY = _scrapy_config.get("autothrottle_start_delay", 0.1)
AUTOTHROTTLE_MAX_DELAY = polite_autothrottle_max_delay(_scrapy_config.get("autothrottle_max_delay", 60.0))
AUTOTHROTTLE_TARGET_CONCURRENCY = polite_target_concurrency(
    _scrapy_config.get("autothrottle_target_concurrency", 4.0), CONCURRENT_REQUESTS_PER_DOMAIN
)
AUTOTHROTTLE_DEBUG = _scrapy_config.get("autothrottle_debug", False)
# 429/503 without Retry-After: exponential per-host backoff (RetryAfterMiddleware).
RATE_LIMIT_BACKOFF_MIN = float(_scrapy_config.get("rate_limit_backoff_min", 1.0))
RATE_LIMIT_BACKOFF_MAX = float(_scrapy_config.get("rate_limit_backoff_max", AUTOTHROTTLE_MAX_DELAY))
RATE_LIMIT_COOLDOWN_FACTOR = float(_scrapy_config.get("rate_limit_cooldown_factor", 4.0))

HTTPCACHE_ENABLED = _scrapy_config.get("httpcache_enabled", True)
HTTPCACHE_EXPIRATION_SECS = _scrapy_config.get("httpcache_expiration_secs", 3600)
HTTPCACHE_DIR = PROJECT_ROOT / "data" / "cache" / "scrapy"
# Filesystem storage (one directory per entry) so HttpCacheQuota can prune the
# oldest responses. A DBM cache is a single file that can't shrink while open (#496).
HTTPCACHE_STORAGE = _scrapy_config.get(
    "httpcache_storage", "scrapy.extensions.httpcache.FilesystemCacheStorage"
)
# Disk quota (#496): prune oldest entries down to TARGET_RATIO * MAX_BYTES once
# usage exceeds MAX_BYTES. Checked at spider open, every PRUNE_INTERVAL_SECS, and
# at close. 0 disables pruning; size is still exported as scrapy_httpcache_bytes.
HTTPCACHE_MAX_BYTES = int(_scrapy_config.get("httpcache_max_bytes", 2 * 1024**3))
HTTPCACHE_PRUNE_INTERVAL_SECS = float(_scrapy_config.get("httpcache_prune_interval_secs", 300))
HTTPCACHE_PRUNE_TARGET_RATIO = float(_scrapy_config.get("httpcache_prune_target_ratio", 0.8))

TWISTED_REACTOR = _scrapy_config.get(
    "twisted_reactor", "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
)
FEED_EXPORT_ENCODING = _scrapy_config.get("feed_export_encoding", "utf-8")

# ============================================================================
# ============================================================================
# Request dedup (#197). RFPDupeFilter drops repeat requests by fingerprint
# inside one crawl process, at no network cost, so a Redis blip can no longer
# turn into duplicate fetches. The Redis seen-URL sets stay the cross-process
# and cross-run control; the two are complementary. Seeds, sitemap entries and
# retries that must re-fetch pass dont_filter=True explicitly.
# BaseDupeFilter (no dedup at all) should only be selected via config for
# debugging.
DUPEFILTER_CLASS = _scrapy_config.get("dupefilter_class", "scrapy.dupefilters.RFPDupeFilter")

# #395: config-driven (stage1.fetch_policy.cookies_enabled), default False.
# Cookies carry session/CSRF state and can identify the crawler across pages;
# keep them off unless a section needs a login/session, and then scope them
# with cookies_allowed_domains so every other host stays cookieless.
COOKIES_ENABLED = str(_scrapy_config.get("cookies_enabled", False)).strip().lower() in {"1", "true", "yes", "on"}
COOKIES_ALLOWED_DOMAINS = list(_scrapy_config.get("cookies_allowed_domains") or [])
DEPTH_LIMIT = 10
DEPTH_PRIORITY = 1
DEPTH_STATS_VERBOSE = True

PLAYWRIGHT_BROWSER_TYPE = _scrapy_config.get("playwright_browser_type", "chromium")
PLAYWRIGHT_LAUNCH_OPTIONS = _scrapy_config.get(
    "playwright_launch_options", {"headless": True}
)

# ============================================================================
# ============================================================================
EXTENSIONS = _scrapy_config.get(
    "extensions",
    {
        "src.scrapy_prometheus.PrometheusExtension": 500,
        "src.otel_tracing.OtelTracingExtension": 510,
        # No-op (NotConfigured) unless HTTPCACHE_ENABLED (#496).
        "src.stage1.extensions.httpcache_quota.HttpCacheQuota": 520,
        # #539: drain gracefully before the container's cgroup OOM killer.
        "src.memory_soft_stop.MemorySoftStop": 530,
    },
)
MEMORY_SOFT_STOP_ENABLED = True
MEMORY_SOFT_STOP_FRACTION = float(os.getenv("MEMORY_SOFT_STOP_FRACTION", "0.85"))
MEMORY_SOFT_STOP_INTERVAL = 5.0
MEMORY_SOFT_STOP_LIMIT_MB = int(os.getenv("MEMORY_SOFT_STOP_LIMIT_MB", "0"))

# ============================================================================
# ============================================================================
PROMETHEUS_ENABLED = _scrapy_config.get("prometheus_enabled", True)

PROMETHEUS_PORT = _scrapy_config.get("prometheus_port", 9410)
# All interfaces by design (scraped as scrapy-app:9410 in compose/k8s).
PROMETHEUS_HOST = _scrapy_config.get("prometheus_host", "0.0.0.0")  # nosec B104
PROMETHEUS_PATH = _scrapy_config.get("prometheus_path", "metrics")

# ============================================================================
# OpenTelemetry tracing (no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set
# and optional [otel] packages are installed: pip install -e ".[otel]")
# ============================================================================
OTEL_ENABLED = _scrapy_config.get("otel_enabled", True)

OTEL_SERVICE_NAME = _scrapy_config.get(
    "otel_service_name", os.getenv("OTEL_SERVICE_NAME", "scrapy-pipeline")
)

# ============================================================================
# ============================================================================

KAFKA_BOOTSTRAP_SERVERS = os.getenv(
    "KAFKA_BOOTSTRAP_SERVERS",
    _scrapy_config.get("kafka_bootstrap_servers", "localhost:9092"),
)

KAFKA_TOPIC = os.getenv(
    "KAFKA_TOPIC",
    _scrapy_config.get("kafka_topic", "validated_items"),
)

KAFKA_PRODUCER_CONFIG = _scrapy_config.get("kafka_producer_config", {})
# Message key (#285): records are keyed by this field (url_hash) so a URL always
# maps to one partition. Empty string disables keying.
KAFKA_MESSAGE_KEY_FIELD = os.getenv(
    "KAFKA_MESSAGE_KEY_FIELD",
    _scrapy_config.get("kafka_message_key_field", "url_hash"),
)
# Refuse to start a non-idempotent producer (#464). On in the Helm configmap.
KAFKA_REQUIRE_IDEMPOTENCE = os.getenv(
    "KAFKA_REQUIRE_IDEMPOTENCE",
    str(_scrapy_config.get("kafka_require_idempotence", False)),
).strip().lower() in {"1", "true", "yes", "on"}
# Undeliverable messages (produce errors after retries, async delivery
# failures, anything left after the close flush) are appended to
# KAFKA_SPILL_DIR/<topic>.jsonl instead of being dropped (#175, #249).
KAFKA_SPILL_DIR = os.getenv("KAFKA_SPILL_DIR", _scrapy_config.get("kafka_spill_dir", "data/kafka_spill"))
KAFKA_PRODUCE_RETRIES = int(_scrapy_config.get("kafka_produce_retries", 3))
KAFKA_PRODUCE_RETRY_BACKOFF = float(_scrapy_config.get("kafka_produce_retry_backoff", 0.2))
# Must stay well under the pod's terminationGracePeriodSeconds (see k8s/README.md).
KAFKA_CLOSE_FLUSH_TIMEOUT = float(_scrapy_config.get("kafka_close_flush_timeout", 30.0))

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

VALIDATION_FAILURES_TOPIC = _scrapy_config.get(
    "validation_failures_topic", "validation_failures"
)

# ============================================================================
# ============================================================================
RECENCY_DECAY_CONSTANT = _scrapy_config.get("recency_decay_constant", 0.01)

# Score for items without a usable publication_date. Default None: the score
# stays null ("freshness unknown") instead of a fabricated 0.5 that downstream
# ranking would read as median relevance (#675). Set a float only to opt back
# into legacy imputation.
RECENCY_DEFAULT_SCORE = _scrapy_config.get("recency_default_score", None)

# ============================================================================
# ============================================================================
AGGREGATION_ENABLED = _scrapy_config.get("aggregation_enabled", True)

AGGREGATION_OUTPUT_TOPIC = _scrapy_config.get(
    "aggregation_output_topic", "entity_summaries"
)
# Most-recent items kept in memory per entity, and whether summaries are
# written to the Delta table named by AGGREGATION_OUTPUT_TOPIC (#790).
AGGREGATION_MAX_ITEMS_PER_ENTITY = _scrapy_config.get("aggregation_max_items_per_entity", 10)
AGGREGATION_PERSIST = _scrapy_config.get("aggregation_persist", True)
# Max entity groups held in memory (LRU spill beyond it) and periodic full
# flush every N aggregated items; 0 disables periodic flushing (#201).
AGGREGATION_MAX_ENTITIES = _scrapy_config.get("aggregation_max_entities", 10000)
AGGREGATION_FLUSH_EVERY_ITEMS = _scrapy_config.get("aggregation_flush_every_items", 50000)

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

# Off by default (#470): the ASR pipeline is only registered when enabled, so a
# default crawl never imports speech_recognition. $ASR_ENABLED overrides config.
_asr_env = os.environ.get("ASR_ENABLED")
ASR_ENABLED = (
    _asr_env.strip().lower() in ("1", "true", "yes", "on")
    if _asr_env is not None
    else bool(_scrapy_config.get("asr_enabled", False))
)
if ASR_ENABLED:
    # After metadata/recency, before Kafka, so transcripts ship with the record.
    ITEM_PIPELINES = {**ITEM_PIPELINES, "src.common.async_asr_processor.ASRPipeline": 260}

# Speech-to-text backend: none (default, no egress) | google (uploads audio to
# Google, explicit opt-in) | whisper (local). $ASR_PROVIDER overrides config (#429).
ASR_PROVIDER = os.environ.get("ASR_PROVIDER") or _scrapy_config.get("asr_provider", "none")

# Note: The system uses multiple Kafka topics for architectural decoupling.
# Prefer kafka.topics.* in config.yml (or scrapy.kafka_topic) over ad-hoc defaults.
