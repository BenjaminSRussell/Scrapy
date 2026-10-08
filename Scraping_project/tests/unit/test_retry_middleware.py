"""IntelligentRetryMiddleware status/exception matrix (#242).

Deterministic: ``_rng`` is seeded and ``reactor.callLater`` is patched so no
sleeps or Twisted scheduling happen. The table below is the contract.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import Request, Response
from scrapy.settings import Settings
from twisted.internet.error import (
    ConnectError,
    ConnectionDone,
    ConnectionLost,
    DNSLookupError,
    TCPTimedOutError,
    TimeoutError as TwistedTimeoutError,
)

from src.stage1.middlewares.retry_middleware import IntelligentRetryMiddleware


def _settings(**overrides):
    """Real Scrapy settings (default RETRY_EXCEPTIONS) with our overrides."""
    values = {
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        "RETRY_BACKOFF_BASE": 2,
        "RETRY_BACKOFF_MAX": 60,
    }
    values.update(overrides)
    return Settings(values)


@pytest.fixture
def mw():
    return IntelligentRetryMiddleware(_settings())


# status, expected action from _classify_status, whether process_response retries
STATUS_MATRIX = [
    # Transient: must retry
    (408, "retry", True),
    (429, "retry", True),
    (500, "retry", True),
    (502, "retry", True),
    (503, "retry", True),
    (504, "retry", True),
    # Permanent: return response as-is (give up)
    (400, "fail", False),
    (401, "fail", False),
    (403, "fail", False),
    (404, "fail", False),
    (410, "fail", False),
    # Everything else: pass through
    (200, "pass", False),
    (201, "pass", False),
    (301, "pass", False),
    (418, "pass", False),
]


@pytest.mark.parametrize("status,action,should_retry", STATUS_MATRIX)
def test_status_classification_and_retry(mw, status, action, should_retry):
    assert mw._classify_status(status) == action
    request = Request("https://example.com/x")
    response = Response(url=request.url, status=status, body=b"x")
    spider = MagicMock()
    spider.crawler = None  # skip reactor path
    with patch.object(mw, "_compute_backoff", return_value=0.0):
        result = mw.process_response(request, response, spider)
    if should_retry:
        assert isinstance(result, Request)
        assert result.meta["retry_times"] == 1
        assert result is not request
    else:
        assert result is response


def test_max_retries_gives_up(mw):
    """After RETRY_TIMES attempts, return None (Scrapy drops the request)."""
    request = Request("https://example.com/x", meta={"retry_times": 2})  # next would be 3 > 2
    response = Response(url=request.url, status=503, body=b"x")
    spider = MagicMock(crawler=None)
    with patch.object(mw, "_compute_backoff", return_value=0.0):
        assert mw.process_response(request, response, spider) is None


@pytest.mark.parametrize(
    "exc",
    [
        DNSLookupError(),
        TCPTimedOutError(),
        TwistedTimeoutError(),
        ConnectionLost(),
        ConnectionDone(),
        ConnectError(),
    ],
)
def test_retryable_exceptions(mw, exc):
    request = Request("https://example.com/x")
    spider = MagicMock(crawler=None)
    with patch.object(mw, "_compute_backoff", return_value=0.0):
        result = mw.process_exception(request, exc, spider)
    assert isinstance(result, Request)
    assert result.meta["retry_times"] == 1


def test_non_retryable_exception_returns_none(mw):
    request = Request("https://example.com/x")
    spider = MagicMock(crawler=None)
    assert mw.process_exception(request, ValueError("nope"), spider) is None


def test_backoff_is_deterministic_and_capped(mw):
    # Seeded Random(42): two fresh middlewares produce the same sequence; no sleeps.
    other = IntelligentRetryMiddleware(_settings())
    a = [mw._compute_backoff(i) for i in range(1, 6)]
    b = [other._compute_backoff(i) for i in range(1, 6)]
    assert a == b
    assert all(0 < d <= mw.backoff_max for d in a)
    assert mw._compute_backoff(10_000) <= mw.backoff_max


def test_no_random_import_in_calculate_backoff(mw):
    with patch("builtins.__import__") as mock_import:
        mw._calculate_backoff_delay(1)
        for call in mock_import.call_args_list:
            assert call.args[0] != "random"
