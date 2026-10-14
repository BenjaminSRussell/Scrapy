"""#198: Stage 1 proxy rotation is config-driven, and failed proxies are quarantined."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response
from scrapy.settings import Settings

from src.core.config import Config
from src.settings import PROJECT_ROOT, derive_scrapy_config
from src.stage1.middlewares.proxy_rotation_middleware import (
    MIDDLEWARE_PATH,
    MIDDLEWARE_PRIORITY,
    PROXY_FAILURES,
    ProxyRotationMiddleware,
    is_proxy_timeout,
    parse_proxy_list,
    proxy_settings_from_sources,
    redact_proxy,
    resolve_proxy_rotation_enabled,
)
from src.stage1.middlewares.spider_config import get_spider_settings

A = "http://proxy-a.example:8080"
B = "http://proxy-b.example:8080"
SECRET = "http://alice:s3cret@proxy-a.example:8080"
SPIDER = SimpleNamespace(name="scout")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Stats:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def inc_value(self, key: str, count: int = 1, start: int = 0) -> None:
        self.values[key] = self.values.get(key, start) + count

    def set_value(self, key: str, value: int) -> None:
        self.values[key] = value


def _mw(proxies: list[str] | None = None, **kwargs: object) -> tuple[ProxyRotationMiddleware, Clock, Stats]:
    clock = Clock()
    stats = Stats()
    mw = ProxyRotationMiddleware(
        proxies if proxies is not None else [A, B],
        enabled=bool(kwargs.get("enabled", True)),
        quarantine_seconds=float(kwargs.get("quarantine_seconds", 300)),  # type: ignore[arg-type]
        max_rotations=int(kwargs.get("max_rotations", 3)),  # type: ignore[arg-type]
        clock=clock,
        stats=stats,
    )
    return mw, clock, stats


def _req(url: str = "https://example.edu/a", **meta: object) -> Request:
    return Request(url, meta=dict(meta))


class TCPTimedOutError(Exception):
    """Stand-in for twisted.internet.error.TCPTimedOutError (name is the contract)."""


class FakeConfig:
    def __init__(self, values: dict, section: dict | None = None):
        self.values = values
        self.section = section or {}

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def get_section(self, name: str) -> dict:
        return dict(self.section) if name == "scrapy" else {}


def test_parse_proxy_list_normalizes_and_drops_junk():
    assert parse_proxy_list(f" {A}, , ftp://nope, {B}, {A}") == [A, B]
    assert parse_proxy_list(["proxy.internal:8080", "https://secure.example:443"]) == [
        "http://proxy.internal:8080",
        "https://secure.example:443",
    ]
    assert parse_proxy_list(None) == []
    assert parse_proxy_list(5) == []
    assert "s3cret" not in redact_proxy(SECRET)
    assert redact_proxy(SECRET) == "http://***@proxy-a.example:8080"


@pytest.mark.parametrize(
    "flag,proxies,expected",
    [
        (None, [], False),
        (True, [], False),
        (None, [A], True),  # list alone enables; no code edit
        (False, [A], False),
        ("0", [A], False),
        ("yes", [A], True),
        ("true", [A], True),
    ],
)
def test_resolve_enable_flag(flag, proxies, expected):
    assert resolve_proxy_rotation_enabled(flag, proxies) is expected


def test_stage1_proxy_config_enables_rotation_without_code_edits():
    derived = derive_scrapy_config(
        FakeConfig(
            {
                "stage1.proxy.urls": [A, B],
                "stage1.proxy.quarantine_seconds": 15,
                "stage1.proxy.max_rotations": 2,
            }
        )
    )
    resolved = proxy_settings_from_sources(derived, env={})
    assert derived["proxy_list"] == [A, B]
    assert "proxy_rotation_enabled" not in derived  # omitted flag: the list is the switch
    assert resolved["PROXY_ROTATION_ENABLED"] is True
    assert resolved["PROXY_LIST"] == [A, B]
    assert resolved["PROXY_QUARANTINE_SECONDS"] == 15
    assert resolved["PROXY_MAX_ROTATIONS"] == 2


def test_explicit_false_and_env_and_scrapy_section_win():
    killed = derive_scrapy_config(
        FakeConfig({"stage1.proxy.enabled": False, "stage1.proxy.urls": [A]})
    )
    assert proxy_settings_from_sources(killed, env={})["PROXY_ROTATION_ENABLED"] is False

    env_off = proxy_settings_from_sources(
        {"proxy_list": [A], "proxy_rotation_enabled": True},
        env={"PROXY_LIST": f"{B}", "PROXY_ROTATION_ENABLED": "0"},
    )
    assert env_off["PROXY_ROTATION_ENABLED"] is False
    assert env_off["PROXY_LIST"] == [B]

    # scrapy.* is an explicit override and beats stage1.proxy.*.
    explicit = derive_scrapy_config(
        FakeConfig(
            {"stage1.proxy.urls": [A], "stage1.proxy.enabled": False},
            section={"proxy_list": [B], "proxy_rotation_enabled": True},
        )
    )
    assert explicit["proxy_list"] == [B]
    assert explicit["proxy_rotation_enabled"] is True
    assert proxy_settings_from_sources(explicit, env={})["PROXY_ROTATION_ENABLED"] is True


def test_committed_config_leaves_rotation_off_and_registers_middleware():
    raw = yaml.safe_load((PROJECT_ROOT / "config.yml").read_text())
    assert "proxy" not in raw.get("stage1", {})
    derived = derive_scrapy_config(Config(PROJECT_ROOT / "config.yml"))
    resolved = proxy_settings_from_sources(derived, env={})
    assert resolved["PROXY_ROTATION_ENABLED"] is False
    assert resolved["PROXY_LIST"] == []

    from src import settings

    assert settings.DOWNLOADER_MIDDLEWARES[MIDDLEWARE_PATH] == MIDDLEWARE_PRIORITY
    assert get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"][MIDDLEWARE_PATH] == MIDDLEWARE_PRIORITY
    # Just before Scrapy's HttpProxyMiddleware (750).
    assert MIDDLEWARE_PRIORITY < 750


def test_from_crawler_reads_settings():
    crawler = SimpleNamespace(
        settings=Settings(
            {
                "PROXY_ROTATION_ENABLED": True,
                "PROXY_LIST": f"{A},{B}",
                "PROXY_QUARANTINE_SECONDS": 12,
                "PROXY_MAX_ROTATIONS": 2,
            }
        ),
        stats=Stats(),
    )
    mw = ProxyRotationMiddleware.from_crawler(crawler)
    assert mw.enabled is True
    assert mw.pool.proxies == [A, B]
    assert mw.pool.quarantine_seconds == 12
    assert mw.max_rotations == 2

    off = ProxyRotationMiddleware.from_crawler(
        SimpleNamespace(settings=Settings({"PROXY_ROTATION_ENABLED": False, "PROXY_LIST": A}), stats=None)
    )
    assert off.enabled is False


def test_disabled_middleware_does_not_touch_the_request():
    mw, _, stats = _mw(enabled=False)
    req = _req()
    assert mw.process_request(req, SPIDER) is None
    assert "proxy" not in req.meta
    resp = Response(req.url, status=407)
    assert mw.process_response(req, resp, SPIDER) is resp
    assert stats.values == {}


def test_round_robin_assigns_proxies_in_order():
    mw, _, stats = _mw()
    seen = []
    for i in range(4):
        req = _req(f"https://example.edu/{i}")
        mw.process_request(req, SPIDER)
        seen.append(req.meta["proxy"])
    assert seen == [A, B, A, B]
    assert stats.values["proxy_rotation/assigned"] == 4


def test_caller_supplied_proxy_is_not_rotated():
    mw, _, _ = _mw()
    req = _req(proxy="http://manual.example:8080")
    mw.process_request(req, SPIDER)
    assert req.meta["proxy"] == "http://manual.example:8080"
    resp = Response(req.url, status=407)
    assert mw.process_response(req, resp, SPIDER) is resp
    assert mw.pool.quarantined_count() == 0


@pytest.mark.parametrize("status", [407, 429])
def test_failure_quarantines_proxy_and_retries_on_the_next(status):
    mw, clock, stats = _mw()
    before = None if PROXY_FAILURES is None else PROXY_FAILURES.labels(spider="scout", reason=str(status))._value.get()
    req = _req()
    mw.process_request(req, SPIDER)
    assert req.meta["proxy"] == A
    req.headers["Proxy-Authorization"] = "Basic old"
    retry = mw.process_response(req, Response(req.url, status=status), SPIDER)
    assert isinstance(retry, Request)
    assert retry.dont_filter is True
    assert "Proxy-Authorization" not in retry.headers
    assert mw.pool.is_quarantined(A)
    assert not mw.pool.is_quarantined(B)
    mw.process_request(retry, SPIDER)
    assert retry.meta["proxy"] == B
    assert stats.values[f"proxy_rotation/failures/{status}"] == 1
    assert stats.values["proxy_rotation/quarantined"] == 1
    assert stats.values["proxy_rotation/rotations"] == 1
    if before is not None:
        after = PROXY_FAILURES.labels(spider="scout", reason=str(status))._value.get()
        assert after == before + 1

    # Still quarantined: only B is eligible.
    for i in range(3):
        later = _req(f"https://example.edu/later-{status}-{i}")
        mw.process_request(later, SPIDER)
        assert later.meta["proxy"] == B

    clock.t += 300
    seen = set()
    for i in range(4):
        later = _req(f"https://example.edu/after-{status}-{i}")
        mw.process_request(later, SPIDER)
        seen.add(later.meta["proxy"])
    assert A in seen
    assert stats.values["proxy_rotation/quarantined"] == 0


def test_timeout_rotates_other_exceptions_do_not():
    assert is_proxy_timeout(TimeoutError())
    assert is_proxy_timeout(TCPTimedOutError())
    assert not is_proxy_timeout(ConnectionRefusedError())

    mw, _, stats = _mw()
    req = _req()
    mw.process_request(req, SPIDER)
    assert mw.process_exception(req, ConnectionRefusedError(), SPIDER) is None
    assert not mw.pool.is_quarantined(A)

    retry = mw.process_exception(req, TimeoutError("download timed out"), SPIDER)
    assert isinstance(retry, Request)
    mw.process_request(retry, SPIDER)
    assert retry.meta["proxy"] == B
    assert stats.values["proxy_rotation/failures/timeout"] == 1


def test_other_statuses_do_not_quarantine():
    mw, _, _ = _mw()
    req = _req()
    mw.process_request(req, SPIDER)
    resp = Response(req.url, status=503)
    assert mw.process_response(req, resp, SPIDER) is resp
    assert mw.pool.quarantined_count() == 0


def test_max_rotations_stops_and_sets_dont_retry():
    mw, _, _ = _mw(max_rotations=1)
    req = _req()
    mw.process_request(req, SPIDER)
    retry = mw.process_response(req, Response(req.url, status=429), SPIDER)
    assert isinstance(retry, Request)
    mw.process_request(retry, SPIDER)
    resp = Response(retry.url, status=429)
    assert mw.process_response(retry, resp, SPIDER) is resp
    assert retry.meta["dont_retry"] is True


def test_all_proxies_quarantined_does_not_fall_back_to_direct_ip():
    mw, clock, stats = _mw([A], max_rotations=3)
    req = _req()
    mw.process_request(req, SPIDER)
    resp = Response(req.url, status=407)
    assert mw.process_response(req, resp, SPIDER) is resp
    assert req.meta["dont_retry"] is True
    with pytest.raises(IgnoreRequest, match="no healthy proxy"):
        mw.process_request(_req("https://example.edu/b"), SPIDER)
    assert stats.values["proxy_rotation/no_healthy_proxy"] == 1
    clock.t += 300
    resumed = _req("https://example.edu/c")
    mw.process_request(resumed, SPIDER)
    assert resumed.meta["proxy"] == A


def test_quarantine_log_redacts_proxy_credentials(caplog):
    mw, _, _ = _mw([SECRET, B])
    req = _req()
    mw.process_request(req, SPIDER)
    assert req.meta["proxy"] == SECRET  # HttpProxyMiddleware still needs the userinfo
    with caplog.at_level("WARNING"):
        mw.process_response(req, Response(req.url, status=407), SPIDER)
    assert "s3cret" not in caplog.text
    assert "alice" not in caplog.text
    assert "***@proxy-a.example:8080" in caplog.text
