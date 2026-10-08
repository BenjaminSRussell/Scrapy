"""#253: src/settings.py loads offline and keeps its critical keys and orderings.

Additions are fine; removing or reordering a critical component fails here.
"""

from __future__ import annotations

import pytest
from scrapy.settings import Settings
from scrapy.utils.misc import load_object

from src import settings as project_settings

# Relative order matters: validation/cleansing before the queue handoff, the
# handoff before schema validation (#608), Kafka export after enrichment.
CRITICAL_PIPELINE_ORDER = [
    "src.pipelines.DataValidationPipeline",
    "src.pipelines.DataCleansingPipeline",
    "src.pipelines.QueueItemPipeline",
    "src.pipelines.SchemaValidationPipeline",
    "src.pipelines.MetadataPipeline",
    "src.pipelines.RecencyScoringPipeline",
    "src.pipelines.KafkaPipeline",
]

CRITICAL_KEYS = {
    "BOT_NAME", "SPIDER_MODULES", "ITEM_PIPELINES", "DOWNLOADER_MIDDLEWARES", "EXTENSIONS",
    "USER_AGENT", "ROBOTSTXT_OBEY", "CONCURRENT_REQUESTS", "CONCURRENT_REQUESTS_PER_DOMAIN",
    "DOWNLOAD_DELAY", "DOWNLOAD_TIMEOUT", "RETRY_ENABLED", "RETRY_TIMES",
    "AUTOTHROTTLE_ENABLED", "AUTOTHROTTLE_MAX_DELAY", "AUTOTHROTTLE_TARGET_CONCURRENCY",
    "DUPEFILTER_CLASS", "REQUEST_FINGERPRINTER_CLASS", "TWISTED_REACTOR",
    "HTTPCACHE_ENABLED", "HTTPCACHE_STORAGE", "DEPTH_LIMIT", "COOKIES_ENABLED",
    "KAFKA_BOOTSTRAP_SERVERS", "KAFKA_TOPIC", "KAFKA_REQUIRE_IDEMPOTENCE",
}


def _ordered(components: dict) -> list[str]:
    return [k for k, v in sorted(components.items(), key=lambda kv: kv[1]) if v is not None]


def test_all_critical_keys_are_defined():
    missing = sorted(k for k in CRITICAL_KEYS if not hasattr(project_settings, k))
    assert missing == []


def test_critical_pipelines_present_in_order():
    order = _ordered(project_settings.ITEM_PIPELINES)
    present = [p for p in order if p in CRITICAL_PIPELINE_ORDER]
    assert present == CRITICAL_PIPELINE_ORDER


def test_priorities_are_unique_so_order_is_deterministic():
    for name in ("ITEM_PIPELINES", "DOWNLOADER_MIDDLEWARES", "EXTENSIONS"):
        priorities = [v for v in getattr(project_settings, name).values() if v is not None]
        assert len(priorities) == len(set(priorities)), f"{name} has duplicate priorities"


@pytest.mark.parametrize("name", ["ITEM_PIPELINES", "DOWNLOADER_MIDDLEWARES", "EXTENSIONS"])
def test_every_component_path_imports(name):
    for path, priority in getattr(project_settings, name).items():
        if priority is None:
            continue
        load_object(path)  # raises on a typo / moved class


def test_robots_politeness_middleware_replaces_scrapys():
    mws = project_settings.DOWNLOADER_MIDDLEWARES
    assert mws["scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware"] is None
    assert "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware" in mws
    assert project_settings.ROBOTSTXT_OBEY is True


def test_safety_invariants():
    s = project_settings
    assert s.TWISTED_REACTOR == "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
    assert 0 < s.DOWNLOAD_TIMEOUT <= 120
    assert s.AUTOTHROTTLE_MAX_DELAY >= 30  # #194 floor
    assert s.AUTOTHROTTLE_TARGET_CONCURRENCY <= s.CONCURRENT_REQUESTS_PER_DOMAIN
    assert s.CONCURRENT_REQUESTS_PER_DOMAIN <= s.CONCURRENT_REQUESTS
    assert s.DUPEFILTER_CLASS != "scrapy.dupefilters.BaseDupeFilter"  # #197
    assert s.HTTPCACHE_STORAGE.endswith("FilesystemCacheStorage")  # #496 quota needs it


def test_scrapy_accepts_the_module_as_settings():
    settings = Settings()
    settings.setmodule(project_settings, priority="project")
    assert settings.get("BOT_NAME") == project_settings.BOT_NAME
    assert settings.getdict("ITEM_PIPELINES") == project_settings.ITEM_PIPELINES

