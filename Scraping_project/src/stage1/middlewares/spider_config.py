"""Helpers for assembling Scrapy settings from config.yml."""

import logging
from typing import Any

from src.core.config import Config
from src.stage1.middlewares.fetch_policy_middleware import as_bool

logger = logging.getLogger(__name__)

# #194: AutoThrottle can only slow a host down to AUTOTHROTTLE_MAX_DELAY. With
# the old 1-1.5s caps a 429 storm could never be absorbed, so the production
# floor is 30s (a host is still probed at least every 30s while backing off).
MIN_AUTOTHROTTLE_MAX_DELAY = 30.0


def polite_autothrottle_max_delay(value: Any) -> float:
    """Configured AUTOTHROTTLE_MAX_DELAY, raised to the 30s floor (#194)."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        delay = MIN_AUTOTHROTTLE_MAX_DELAY
    if delay < MIN_AUTOTHROTTLE_MAX_DELAY:
        logger.warning(
            f"autothrottle_max_delay={value} is below the {MIN_AUTOTHROTTLE_MAX_DELAY:g}s politeness floor; "
            f"using {MIN_AUTOTHROTTLE_MAX_DELAY:g}s (#194)"
        )
        return MIN_AUTOTHROTTLE_MAX_DELAY
    return delay


def polite_target_concurrency(value: Any, per_domain: Any) -> float:
    """AUTOTHROTTLE_TARGET_CONCURRENCY, never above the per-domain cap (#194).

    AutoThrottle's target is the average number of parallel requests *per
    remote site*; a target above CONCURRENT_REQUESTS_PER_DOMAIN (the scout
    profile had 2048 vs 512) means AutoThrottle never slows anything down.
    """
    try:
        target = float(value)
    except (TypeError, ValueError):
        target = 1.0
    try:
        cap = float(per_domain)
    except (TypeError, ValueError):
        cap = target
    return max(1.0, min(target, cap))


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
        # #395: cookieless unless the spider's config opts in (was True).
        "COOKIES_ENABLED": as_bool(spider_config.get("cookies_enabled"), default=False),
        "HTTPCACHE_ENABLED": False,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": spider_config.get("retry_times", 3),
        "AUTOTHROTTLE_ENABLED": spider_config.get("autothrottle_enabled", True),
        "AUTOTHROTTLE_START_DELAY": spider_config.get("autothrottle_start_delay", 0.25),
        "AUTOTHROTTLE_MAX_DELAY": polite_autothrottle_max_delay(spider_config.get("autothrottle_max_delay", 60)),
        "AUTOTHROTTLE_TARGET_CONCURRENCY": polite_target_concurrency(
            spider_config.get("autothrottle_target_concurrency", 2.0),
            spider_config.get("concurrent_requests_per_domain", 8),
        ),
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
        # #582: drop captcha/challenge responses; back off a domain on a spike.
        # #186/#188: robots.txt Disallow + Crawl-delay (replaces Scrapy's middleware).
        "ROBOTSTXT_OBEY": bool(spider_config.get("robotstxt_obey", True)),
        "ROBOTS_MAX_CRAWL_DELAY": float(spider_config.get("robots_max_crawl_delay", 60)),
        "DOWNLOADER_MIDDLEWARES": {
            # #456: global kill switch + request/byte budgets, checked before
            # every download (early, so a dropped request costs nothing).
            "src.stage1.middlewares.crawl_guard_middleware.CrawlGuardMiddleware": 25,
            "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
            "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware": 100,
            "src.stage1.middlewares.soft_ban_middleware.SoftBanMiddleware": 540,
            # Before RetryMiddleware (550) on the response path: wait Retry-After.
            "src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware": 560,
            # #395/#396: large-doc timeout + cookie scope; before DownloadTimeout (350).
            "src.stage1.middlewares.fetch_policy_middleware.FetchPolicyMiddleware": 340,
        },
        "RETRY_AFTER_MAX_DELAY": float(spider_config.get("retry_after_max_delay", 120)),
        # #194: 429/503 without Retry-After double the host's delay (>= 1s) up to
        # the AutoThrottle max, held for 4x that delay.
        "RATE_LIMIT_BACKOFF_MIN": float(spider_config.get("rate_limit_backoff_min", 1.0)),
        "RATE_LIMIT_BACKOFF_MAX": float(
            spider_config.get(
                "rate_limit_backoff_max",
                polite_autothrottle_max_delay(spider_config.get("autothrottle_max_delay", 60)),
            )
        ),
        "RATE_LIMIT_COOLDOWN_FACTOR": float(spider_config.get("rate_limit_cooldown_factor", 4.0)),
        "SOFT_BAN_SLOT_DELAY": spider_config.get("soft_ban_slot_delay", 30.0),
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.depth.DepthMiddleware": 900,
        },
    }

    return settings
