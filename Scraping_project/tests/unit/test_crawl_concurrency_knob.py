"""#787: one documented crawl-concurrency knob; duplicates warn and lose."""

from __future__ import annotations

import logging

import pytest
import yaml

from src.core.config import Config, reset_config
from src.settings import CRAWL_CONCURRENCY_KEYS, PROJECT_ROOT, crawl_knob_warnings, derive_scrapy_config


def _cfg(tmp_path, data) -> Config:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data))
    reset_config()
    return Config(path)


SCOUT = {"concurrent_requests": 100, "concurrent_requests_per_domain": 10, "download_delay": 0.5, "autothrottle_target_concurrency": 7}


def test_canonical_knob_wins_over_differing_scrapy_duplicate(tmp_path, caplog):
    cfg = _cfg(tmp_path, {"scrapy": {"concurrent_requests": 4, "download_delay": 3.0}, "stage1": {"spiders": {"scout": SCOUT}}})
    with caplog.at_level(logging.WARNING, logger="src.settings"):
        scrapy = derive_scrapy_config(cfg)
    assert scrapy["concurrent_requests"] == 100
    assert scrapy["download_delay"] == 0.5
    assert "scrapy.concurrent_requests=4 but stage1.spiders.scout.concurrent_requests=100; using 100" in caplog.text
    assert "scrapy.download_delay=3.0" in caplog.text


def test_equal_duplicate_is_silent(tmp_path, caplog):
    cfg = _cfg(tmp_path, {"scrapy": {"concurrent_requests": 100}, "stage1": {"spiders": {"scout": SCOUT}}})
    with caplog.at_level(logging.WARNING, logger="src.settings"):
        scrapy = derive_scrapy_config(cfg)
    assert scrapy["concurrent_requests"] == 100
    assert crawl_knob_warnings(cfg) == []
    assert "#787" not in caplog.text


def test_legacy_only_is_honoured_but_flagged(tmp_path):
    cfg = _cfg(tmp_path, {"scrapy": {"concurrent_requests_per_domain": 3}})
    assert derive_scrapy_config(cfg)["concurrent_requests_per_domain"] == 3
    (msg,) = crawl_knob_warnings(cfg)
    assert "scrapy.concurrent_requests_per_domain is deprecated" in msg
    assert "stage1.spiders.scout.concurrent_requests_per_domain" in msg


def test_non_concurrency_scrapy_keys_still_win(tmp_path):
    # only the duplicated concurrency knobs changed precedence
    cfg = _cfg(tmp_path, {"scrapy": {"download_timeout": 99}, "stage1": {"spiders": {"scout": {**SCOUT, "download_timeout": 15}}}})
    assert derive_scrapy_config(cfg)["download_timeout"] == 99


def test_autothrottle_target_bridged_from_profile(tmp_path):
    cfg = _cfg(tmp_path, {"stage1": {"spiders": {"scout": SCOUT}}})
    assert derive_scrapy_config(cfg)["autothrottle_target_concurrency"] == 7


def test_dead_depth_spider_knob_warns(tmp_path):
    cfg = _cfg(tmp_path, {"stage1": {"depth_spider": {"concurrent_requests": 64}, "spiders": {"deep_dive": {"concurrent_requests": 32}}}})
    (msg,) = crawl_knob_warnings(cfg)
    assert "stage1.depth_spider.concurrent_requests=64 is ignored" in msg
    assert "stage1.spiders.deep_dive.concurrent_requests (currently 32)" in msg


@pytest.mark.parametrize("key", CRAWL_CONCURRENCY_KEYS)
def test_every_key_detected_when_conflicting(tmp_path, key):
    cfg = _cfg(tmp_path, {"scrapy": {key: 1}, "stage1": {"spiders": {"scout": {key: 2}}}})
    assert len(crawl_knob_warnings(cfg)) == 1
    assert derive_scrapy_config(cfg)[key] == 2


# --- shipped config ---------------------------------------------------------------


def test_shipped_config_has_no_duplicate_or_dead_knobs():
    reset_config()
    assert crawl_knob_warnings(Config(PROJECT_ROOT / "config.yml")) == []


def test_project_defaults_equal_scout_profile_and_spiders_use_their_profile():
    """Operators tune stage1.spiders.<profile>.*; every consumer sees that number."""
    import src.settings as settings
    from src.stage1.experimental.depth_spider import DepthSpider
    from src.stage1.scout_spider import ScoutSpider

    raw = yaml.safe_load((PROJECT_ROOT / "config.yml").read_text())
    scout = raw["stage1"]["spiders"]["scout"]
    deep = raw["stage1"]["spiders"]["deep_dive"]
    assert settings.CONCURRENT_REQUESTS == scout["concurrent_requests"] == ScoutSpider.custom_settings["CONCURRENT_REQUESTS"]
    assert settings.CONCURRENT_REQUESTS_PER_DOMAIN == scout["concurrent_requests_per_domain"]
    assert settings.DOWNLOAD_DELAY == scout["download_delay"] == ScoutSpider.custom_settings["DOWNLOAD_DELAY"]
    assert settings.AUTOTHROTTLE_TARGET_CONCURRENCY == float(scout["autothrottle_target_concurrency"])
    assert DepthSpider.custom_settings["CONCURRENT_REQUESTS"] == deep["concurrent_requests"]
    assert "concurrent_requests" not in raw["stage1"]["depth_spider"]
