"""#212: with_retry works on plain (sync) functions with the same policy as async."""

import asyncio

import pytest

import src.utils.retry as retry_mod
from src.core.exceptions import (
    CircuitBreakerOpen,
    DataValidationError,
    MaxRetriesExceeded,
    NetworkError,
    RateLimitError,
)
from src.utils.retry import CircuitBreaker, with_retry


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", calls.append)
    return calls


def flaky(failures, exc=lambda: NetworkError("blip"), result="ok"):
    state = {"calls": 0}

    def fn(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] <= failures:
            raise exc()
        return (result, args, kwargs)

    return fn, state


def test_sync_success_after_one_failure(sleeps):
    fn, state = flaky(1)
    wrapped = with_retry(max_attempts=3, base_delay=0.5, jitter=False, retry_on=(NetworkError,))(fn)
    assert wrapped(1, k=2) == ("ok", (1,), {"k": 2})
    assert state["calls"] == 2 and sleeps == [0.5]


def test_sync_exponential_backoff_capped(sleeps):
    fn, _ = flaky(10)
    wrapped = with_retry(max_attempts=5, base_delay=1, max_delay=3, jitter=False, retry_on=(NetworkError,))(fn)
    with pytest.raises(MaxRetriesExceeded):
        wrapped()
    assert sleeps == [1, 2, 3, 3]  # no sleep after the last attempt


def test_sync_max_retries_exceeded_keeps_original(sleeps):
    fn, state = flaky(10)
    wrapped = with_retry(max_attempts=3, base_delay=0.01, retry_on=(NetworkError,))(fn)
    with pytest.raises(MaxRetriesExceeded) as info:
        wrapped()
    assert state["calls"] == 3
    assert info.value.context["attempts"] == 3
    assert isinstance(info.value.original_exception, NetworkError)


def test_sync_jitter_stays_in_band(sleeps):
    fn, _ = flaky(1)
    with_retry(max_attempts=2, base_delay=2.0, jitter=True, retry_on=(NetworkError,))(fn)()
    assert 1.0 <= sleeps[0] <= 3.0


def test_sync_non_retryable_raises_immediately(sleeps):
    fn, state = flaky(5, exc=lambda: DataValidationError("bad"))
    wrapped = with_retry(max_attempts=3, retry_on=(Exception,))(fn)
    with pytest.raises(DataValidationError):
        wrapped()
    assert state["calls"] == 1 and sleeps == []


def test_sync_unlisted_exception_propagates(sleeps):
    fn, state = flaky(5, exc=lambda: KeyError("x"))
    wrapped = with_retry(max_attempts=3, retry_on=(NetworkError,))(fn)
    with pytest.raises(KeyError):
        wrapped()
    assert state["calls"] == 1


def test_sync_rate_limit_retry_after(sleeps):
    fn, _ = flaky(1, exc=lambda: RateLimitError("slow", retry_after=7))
    with_retry(max_attempts=2, base_delay=0.1, max_delay=60, jitter=False, retry_on=(RateLimitError,))(fn)()
    assert sleeps == [7.0]


def test_rate_limit_without_retry_after_falls_back_to_backoff(sleeps):
    fn, _ = flaky(1, exc=lambda: RateLimitError("slow"))  # retry_after=None used to raise TypeError
    with_retry(max_attempts=2, base_delay=0.25, jitter=False, retry_on=(RateLimitError,))(fn)()
    assert sleeps == [0.25]


def test_sync_circuit_breaker_opens(sleeps):
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60, name="sync")
    fn, state = flaky(10)
    wrapped = with_retry(max_attempts=5, base_delay=0.01, circuit_breaker=cb, retry_on=(NetworkError,))(fn)
    with pytest.raises(CircuitBreakerOpen):
        wrapped()
    assert state["calls"] == 2 and cb.state == "open"


def test_sync_success_resets_breaker(sleeps):
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60, name="sync2")
    fn, _ = flaky(2)
    with_retry(max_attempts=3, base_delay=0.01, circuit_breaker=cb, retry_on=(NetworkError,))(fn)()
    assert cb.failure_count == 0 and cb.state == "closed"


def test_decorated_sync_keeps_metadata_and_is_not_coroutine():
    @with_retry(max_attempts=2)
    def named():
        """doc"""
        return 1

    assert named.__name__ == "named" and named.__doc__ == "doc"
    assert not asyncio.iscoroutinefunction(named)
    assert named() == 1


async def test_async_path_unchanged(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    @with_retry(max_attempts=3, base_delay=0.5, jitter=False, retry_on=(NetworkError,))
    async def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            raise NetworkError("blip")
        return "ok"

    assert asyncio.iscoroutinefunction(fetch)
    assert await fetch() == "ok" and delays == [0.5]
