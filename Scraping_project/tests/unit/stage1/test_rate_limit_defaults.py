"""Production politeness defaults (#194)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from src.stage1.middlewares.spider_config import (
    MIN_AUTOTHROTTLE_MAX_DELAY,
    get_spider_settings,
    polite_autothrottle_max_delay,
    polite_target_concurrency,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("spider", ["scout", "deep_dive"])
def test_spider_profiles_can_back_off_at_least_30s(spider):
    s = get_spider_settings(spider)
    assert s["AUTOTHROTTLE_ENABLED"] is True
    assert s["AUTOTHROTTLE_MAX_DELAY"] >= 30
    assert s["RATE_LIMIT_BACKOFF_MAX"] >= 30


@pytest.mark.parametrize("spider", ["scout", "deep_dive"])
def test_target_concurrency_never_exceeds_per_domain_cap(spider):
    s = get_spider_settings(spider)
    assert 1 <= s["AUTOTHROTTLE_TARGET_CONCURRENCY"] <= s["CONCURRENT_REQUESTS_PER_DOMAIN"]


def test_project_settings_defaults():
    import src.settings as settings

    assert settings.AUTOTHROTTLE_MAX_DELAY >= 30
    assert settings.AUTOTHROTTLE_TARGET_CONCURRENCY <= settings.CONCURRENT_REQUESTS_PER_DOMAIN
    assert settings.RATE_LIMIT_BACKOFF_MAX >= 30
    assert settings.RATE_LIMIT_BACKOFF_MIN > 0


def test_shipped_config_documents_per_domain_caps():
    spiders = yaml.safe_load((ROOT / "config.yml").read_text())["stage1"]["spiders"]
    for name, cfg in spiders.items():
        assert cfg["concurrent_requests_per_domain"] <= 16, name
        assert cfg["autothrottle_max_delay"] >= 30, name
        assert cfg["autothrottle_target_concurrency"] <= cfg["concurrent_requests_per_domain"], name


def test_low_max_delay_is_raised_to_floor_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert polite_autothrottle_max_delay(1.5) == MIN_AUTOTHROTTLE_MAX_DELAY
    assert "politeness floor" in caplog.text
    assert polite_autothrottle_max_delay(90) == 90
    assert polite_autothrottle_max_delay("junk") == MIN_AUTOTHROTTLE_MAX_DELAY


@pytest.mark.parametrize(
    "target,per_domain,expected",
    [(2048, 512, 512), (8, 16, 8), (0.2, 16, 1.0), ("junk", 16, 1.0), (4, None, 4)],
)
def test_polite_target_concurrency(target, per_domain, expected):
    assert polite_target_concurrency(target, per_domain) == expected
