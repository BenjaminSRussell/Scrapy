"""#241: spider_config.get_spider_settings defaults, overrides and guards (no I/O)."""

from __future__ import annotations

import pytest

from src.stage1.middlewares import spider_config
from src.stage1.middlewares.spider_config import (
    MIN_AUTOTHROTTLE_MAX_DELAY,
    get_spider_settings,
    polite_autothrottle_max_delay,
    polite_target_concurrency,
)


class _Config:
    def __init__(self, spiders):
        self.raw = {"stage1": {"spiders": spiders}}

    def get_raw_config(self):
        return self.raw


@pytest.fixture
def with_spiders(monkeypatch):
    def install(spiders):
        cfg = _Config(spiders)
        monkeypatch.setattr(spider_config.Config, "get_instance", classmethod(lambda cls: cfg))

    return install


DEFAULTS = {
    "CONCURRENT_REQUESTS": 32,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 8,
    "DOWNLOAD_DELAY": 0.25,
    "DOWNLOAD_TIMEOUT": 30,
    "RETRY_TIMES": 3,
    "DEPTH_LIMIT": 10,
    "DNS_TIMEOUT": 15,
    "MEMUSAGE_LIMIT_MB": 4096,
    "MEMUSAGE_WARNING_MB": 3072,
    "REACTOR_THREADPOOL_MAXSIZE": 20,
    "ROBOTSTXT_OBEY": True,
    "ROBOTS_MAX_CRAWL_DELAY": 60.0,
    "RETRY_AFTER_MAX_DELAY": 120.0,
    "RATE_LIMIT_BACKOFF_MIN": 1.0,
    "RATE_LIMIT_COOLDOWN_FACTOR": 4.0,
    "AUTOTHROTTLE_ENABLED": True,
    "AUTOTHROTTLE_MAX_DELAY": 60.0,
    "HTTPCACHE_ENABLED": False,
    "RETRY_ENABLED": True,
}


def test_defaults_when_the_spider_block_sets_almost_nothing(with_spiders):
    with_spiders({"lab": {"depth_limit": 10}})
    settings = get_spider_settings("lab")
    for key, value in DEFAULTS.items():
        assert settings[key] == value, key
    # Target concurrency default 2.0, capped by the per-domain default 8.
    assert settings["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 2.0
    assert settings["RATE_LIMIT_BACKOFF_MAX"] == 60.0  # follows the AutoThrottle max


def test_every_configured_key_overrides_its_default(with_spiders):
    with_spiders(
        {
            "fast": {
                "concurrent_requests": 512,
                "concurrent_requests_per_domain": 16,
                "download_delay": 0.01,
                "download_timeout": 12,
                "retry_times": 1,
                "depth_limit": 4,
                "dns_timeout": 3,
                "memory_limit_mb": 1000,
                "memory_warning_mb": 800,
                "reactor_threadpool_maxsize": 64,
                "robotstxt_obey": False,
                "robots_max_crawl_delay": 10,
                "retry_after_max_delay": 30,
                "rate_limit_backoff_min": 2,
                "rate_limit_backoff_max": 90,
                "rate_limit_cooldown_factor": 3,
                "autothrottle_enabled": False,
                "autothrottle_max_delay": 120,
                "autothrottle_target_concurrency": 6,
                "soft_ban_slot_delay": 45,
            }
        }
    )
    s = get_spider_settings("fast")
    assert (s["CONCURRENT_REQUESTS"], s["CONCURRENT_REQUESTS_PER_DOMAIN"]) == (512, 16)
    assert (s["DOWNLOAD_DELAY"], s["DOWNLOAD_TIMEOUT"], s["RETRY_TIMES"], s["DEPTH_LIMIT"]) == (0.01, 12, 1, 4)
    assert (s["DNS_TIMEOUT"], s["MEMUSAGE_LIMIT_MB"], s["MEMUSAGE_WARNING_MB"]) == (3, 1000, 800)
    assert s["REACTOR_THREADPOOL_MAXSIZE"] == 64
    assert s["ROBOTSTXT_OBEY"] is False and s["ROBOTS_MAX_CRAWL_DELAY"] == 10.0
    assert (s["RETRY_AFTER_MAX_DELAY"], s["RATE_LIMIT_BACKOFF_MIN"], s["RATE_LIMIT_BACKOFF_MAX"]) == (30.0, 2.0, 90.0)
    assert s["RATE_LIMIT_COOLDOWN_FACTOR"] == 3.0
    assert s["AUTOTHROTTLE_ENABLED"] is False
    assert s["AUTOTHROTTLE_MAX_DELAY"] == 120.0
    assert s["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 6.0
    assert s["SOFT_BAN_SLOT_DELAY"] == 45


def test_politeness_guards_apply_to_configured_values(with_spiders):
    with_spiders({"rude": {"autothrottle_max_delay": 1, "autothrottle_target_concurrency": 64,
                           "concurrent_requests_per_domain": 4}})
    s = get_spider_settings("rude")
    assert s["AUTOTHROTTLE_MAX_DELAY"] == MIN_AUTOTHROTTLE_MAX_DELAY
    assert s["AUTOTHROTTLE_TARGET_CONCURRENCY"] == 4.0


def test_robots_and_retry_after_middlewares_always_registered(with_spiders):
    with_spiders({"lab": {"depth_limit": 1}})
    mws = get_spider_settings("lab")["DOWNLOADER_MIDDLEWARES"]
    assert mws["scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware"] is None
    assert mws["src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware"] == 100
    assert mws["src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware"] == 560
    assert mws["src.stage1.middlewares.soft_ban_middleware.SoftBanMiddleware"] < 550  # before RetryMiddleware


def test_unknown_or_empty_spider_raises(with_spiders):
    with_spiders({"scout": {"depth_limit": 1}, "empty": {}})
    with pytest.raises(ValueError, match="No configuration found for spider 'ghost'"):
        get_spider_settings("ghost")
    with pytest.raises(ValueError):
        get_spider_settings("empty")


def test_repo_config_spiders_build_without_io():
    # The committed config.yml profiles must all produce valid settings.
    for name in ("scout", "deep_dive"):
        s = get_spider_settings(name)
        assert s["AUTOTHROTTLE_TARGET_CONCURRENCY"] <= s["CONCURRENT_REQUESTS_PER_DOMAIN"]
        assert s["AUTOTHROTTLE_MAX_DELAY"] >= MIN_AUTOTHROTTLE_MAX_DELAY


@pytest.mark.parametrize(
    "value, expected",
    [(60, 60.0), ("45", 45.0), (5, 30.0), (None, 30.0), ("abc", 30.0), (30, 30.0)],
)
def test_polite_autothrottle_max_delay(value, expected):
    assert polite_autothrottle_max_delay(value) == expected


@pytest.mark.parametrize(
    "target, per_domain, expected",
    [(8, 16, 8.0), (64, 4, 4.0), (0.2, 4, 1.0), ("x", 4, 1.0), (3, None, 3.0), (3, "bad", 3.0)],
)
def test_polite_target_concurrency(target, per_domain, expected):
    assert polite_target_concurrency(target, per_domain) == expected
