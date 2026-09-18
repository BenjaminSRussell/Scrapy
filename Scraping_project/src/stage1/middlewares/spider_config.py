"""Helpers for assembling Scrapy settings from config.yml."""

from src.core.config import Config

def get_spider_settings(spider_name: str) -> dict:
    config_instance = Config.get_instance()
    config = config_instance.get_raw_config()
    stage1_config = config.get("stage1", {})
    spider_config = stage1_config.get("spiders", {}).get(spider_name, {})

    if not spider_config:
        raise ValueError(f"No configuration found for spider '{spider_name}' in config.yml")

    settings = {
        "CONCURRENT_REQUESTS": spider_config.get("concurrent_requests", 32),
        "CONCURRENT_REQUESTS_PER_DOMAIN": spider_config.get("concurrent_requests_per_domain", 8),
        "DOWNLOAD_DELAY": spider_config.get("download_delay", 0.25),
        "DOWNLOAD_TIMEOUT": spider_config.get("download_timeout", 30),
        "COOKIES_ENABLED": spider_config.get("cookies_enabled", True),
        "HTTPCACHE_ENABLED": False,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": spider_config.get("retry_times", 3),
        "AUTOTHROTTLE_ENABLED": spider_config.get("autothrottle_enabled", True),
        "AUTOTHROTTLE_START_DELAY": spider_config.get("autothrottle_start_delay", 0.25),
        "AUTOTHROTTLE_MAX_DELAY": spider_config.get("autothrottle_max_delay", 10),
        "AUTOTHROTTLE_TARGET_CONCURRENCY": spider_config.get("autothrottle_target_concurrency", 2.0),
        "REACTOR_THREADPOOL_MAXSIZE": spider_config.get("reactor_threadpool_maxsize", 20),
        "DNS_TIMEOUT": spider_config.get("dns_timeout", 15),
        "MEMUSAGE_ENABLED": True,
        "MEMUSAGE_LIMIT_MB": spider_config.get("memory_limit_mb", 4096),
        "MEMUSAGE_WARNING_MB": spider_config.get("memory_warning_mb", 3072),
        "SCHEDULER_DISK_QUEUE": "scrapy.squeues.PickleFifoDiskQueue",
        "SCHEDULER_PRIORITY_QUEUE": "scrapy.pqueues.ScrapyPriorityQueue",
        "DEPTH_LIMIT": spider_config.get("depth_limit", 10),
        "DEPTH_PRIORITY": 1,
        "DEPTH_STATS_VERBOSE": True,
        "DOWNLOAD_MAXSIZE": 10485760,
        "DOWNLOAD_WARNSIZE": 5242880,
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.depth.DepthMiddleware": 900,
        },
    }

    return settings
