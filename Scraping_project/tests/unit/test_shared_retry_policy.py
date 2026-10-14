"""#215: one retry policy drives Scrapy settings and Stage 2."""

from __future__ import annotations

import os

import pytest

from src.settings import derive_scrapy_config
from src.stage1.middlewares.spider_config import get_spider_settings
from src.stage2.stage2_worker import Stage2Worker
from src.utils.retry import load_retry_policy


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        node = self.data
        for part in str(key).split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get_section(self, name):
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}


class _SpiderConfig:
    def __init__(self, spiders):
        self.raw = {"stage1": {"spiders": spiders}}

    def get_raw_config(self):
        return self.raw


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("APP_ENV", raising=False)


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)


def test_dev_defaults_include_429_and_503(dev_env):
    policy = load_retry_policy(FakeConfig({}), environ={"ENV": "development"})
    assert policy.profile == "development"
    assert policy.attempts == 3
    assert policy.queue_attempts == 3
    assert policy.scrapy_retry_times == 2
    assert policy.backoff_base == 0.5
    assert policy.backoff_max == 8.0
    assert 429 in policy.retryable_statuses
    assert 503 in policy.retryable_statuses
    assert 522 in policy.retryable_statuses  # Scrapy's other transient codes stay
    assert 429 not in policy.in_request_statuses()
    assert 503 in policy.in_request_statuses()
    assert policy.attempts_from_config is False


def test_production_defaults_retry_more(prod_env):
    policy = load_retry_policy(FakeConfig({}), environ={"ENV": "production"})
    assert policy.profile == "production"
    assert policy.attempts == 5
    assert policy.queue_attempts == 5
    assert policy.scrapy_retry_times == 4
    assert policy.backoff_base == 1.0
    assert policy.backoff_max == 30.0
    assert {429, 503} <= policy.retryable_statuses


def test_retry_section_overrides_profile_and_keeps_required_statuses():
    policy = load_retry_policy(
        FakeConfig({
            "retry": {
                "attempts": 6,
                "queue_attempts": 2,
                "backoff_base": 1.5,
                "backoff_max": 0.25,  # raised to the base
                "retryable_statuses": [500, 418],
            }
        }),
        environ={"ENV": "development"},
    )
    assert policy.attempts_from_config is True
    assert policy.attempts == 6
    assert policy.queue_attempts == 2
    assert policy.scrapy_retry_times == 5
    assert policy.backoff_base == 1.5
    assert policy.backoff_max == 1.5
    assert {500, 418, 429, 503} <= policy.retryable_statuses
    assert 522 not in policy.retryable_statuses
    assert 418 in policy.in_request_statuses()
    assert 429 not in policy.in_request_statuses()


def test_invalid_retry_section_falls_back_to_the_profile():
    policy = load_retry_policy(
        FakeConfig({
            "retry": {
                "attempts": 0,
                "queue_attempts": True,
                "backoff_base": -1,
                "backoff_max": "slow",
                "retryable_statuses": "nope",
            }
        }),
        environ={"ENV": "development"},
    )
    assert policy.attempts == 3
    assert policy.queue_attempts == 3
    assert policy.backoff_base == 0.5
    assert policy.backoff_max == 8.0
    assert policy.attempts_from_config is False
    assert 429 in policy.retryable_statuses and 503 in policy.retryable_statuses


def test_dev_scrapy_keeps_scout_retry_times_until_the_section_is_set(dev_env):
    scout = FakeConfig({"stage1": {"spiders": {"scout": {"retry_times": 2}}}})
    assert derive_scrapy_config(scout)["retry_times"] == 2
    explicit = FakeConfig({
        "scrapy": {"retry_times": 7, "retry_http_codes": [500]},
        "stage1": {"spiders": {"scout": {"retry_times": 2}}},
    })
    derived = derive_scrapy_config(explicit)
    assert derived["retry_times"] == 7
    assert 429 in derived["retry_http_codes"] and 503 in derived["retry_http_codes"] and 500 in derived["retry_http_codes"]


def test_production_and_retry_section_drive_scrapy_retry_times(prod_env):
    scout = FakeConfig({"stage1": {"spiders": {"scout": {"retry_times": 2}}}})
    assert derive_scrapy_config(scout)["retry_times"] == 4
    section = FakeConfig({
        "scrapy": {"retry_times": 7},
        "retry": {"attempts": 6},
        "stage1": {"spiders": {"scout": {"retry_times": 2}}},
    })
    derived = derive_scrapy_config(section)
    assert derived["retry_times"] == 5  # section wins over scrapy.retry_times and the prod floor
    assert 429 in derived["retry_http_codes"] and 503 in derived["retry_http_codes"]


def test_project_settings_list_429_and_503():
    from src import settings

    assert 429 in settings.RETRY_HTTP_CODES
    assert 503 in settings.RETRY_HTTP_CODES
    # settings is imported once, under this process's ENV (unset in CI and here).
    if os.getenv("ENV", "development").strip().lower() in {"production", "prod"}:
        assert settings.RETRY_TIMES == 4
    else:
        assert settings.RETRY_TIMES == 2


def _install_spiders(monkeypatch, spiders):
    from src.stage1.middlewares import spider_config

    cfg = _SpiderConfig(spiders)
    monkeypatch.setattr(spider_config.Config, "get_instance", classmethod(lambda cls: cfg))


def test_dev_spider_retry_times_still_override(dev_env, monkeypatch):
    _install_spiders(monkeypatch, {"lab": {"depth_limit": 1}, "fast": {"retry_times": 1, "depth_limit": 1}})
    assert get_spider_settings("lab")["RETRY_TIMES"] == 3
    fast = get_spider_settings("fast")
    assert fast["RETRY_TIMES"] == 1
    assert 429 in fast["RETRY_HTTP_CODES"] and 503 in fast["RETRY_HTTP_CODES"]


def test_production_floors_spider_retry_times(prod_env, monkeypatch):
    _install_spiders(monkeypatch, {"scout": {"retry_times": 2, "depth_limit": 1}})
    assert get_spider_settings("scout")["RETRY_TIMES"] == 4


def test_retry_section_replaces_spider_retry_times(dev_env, monkeypatch):
    policy = load_retry_policy(
        FakeConfig({"retry": {"attempts": 6}}),
        environ={"ENV": "development"},
    )
    monkeypatch.setattr("src.utils.retry.load_retry_policy", lambda *args, **kwargs: policy)
    _install_spiders(monkeypatch, {"fast": {"retry_times": 1, "depth_limit": 1}})
    assert get_spider_settings("fast")["RETRY_TIMES"] == 5


def _quiet_stage2_env(monkeypatch):
    for name in (
        "STAGE2_HTTP_ATTEMPTS",
        "STAGE2_MAX_RETRIES",
        "STAGE2_HTTP_BACKOFF_BASE",
        "STAGE2_HTTP_BACKOFF_MAX",
    ):
        monkeypatch.delenv(name, raising=False)


def test_stage2_dev_defaults_match_the_old_constants(dev_env, monkeypatch):
    _quiet_stage2_env(monkeypatch)
    worker = Stage2Worker(max_concurrent=1)
    assert worker.http_attempts == 3
    assert worker.max_retries == 3
    assert worker.http_backoff_base == 0.5
    assert worker.http_backoff_max == 8.0
    assert 503 in worker._in_request_statuses
    assert 429 in worker.retryable_statuses
    assert 429 not in worker._in_request_statuses


def test_stage2_production_uses_the_shared_policy(prod_env, monkeypatch):
    _quiet_stage2_env(monkeypatch)
    worker = Stage2Worker(max_concurrent=1)
    assert worker.http_attempts == 5
    assert worker.max_retries == 5
    assert worker.http_backoff_base == 1.0
    assert worker.http_backoff_max == 30.0
    assert {503, 522, 408} <= set(worker._in_request_statuses)
    assert 429 not in worker._in_request_statuses


def test_stage2_env_still_overrides_the_policy(monkeypatch):
    _quiet_stage2_env(monkeypatch)
    policy = load_retry_policy(
        FakeConfig({"retry": {"attempts": 4, "backoff_base": 0.25, "backoff_max": 3, "retryable_statuses": [418, 503]}}),
        environ={"ENV": "development"},
    )
    monkeypatch.setattr("src.utils.retry.load_retry_policy", lambda *args, **kwargs: policy)
    monkeypatch.setenv("STAGE2_HTTP_ATTEMPTS", "2")
    monkeypatch.setenv("STAGE2_MAX_RETRIES", "7")
    worker = Stage2Worker(max_concurrent=1)
    assert worker.http_attempts == 2
    assert worker.max_retries == 7
    assert worker.http_backoff_base == 0.25
    assert 418 in worker._in_request_statuses
    assert 503 in worker._in_request_statuses
    assert 429 not in worker._in_request_statuses
    assert 429 in worker.retryable_statuses
