"""#171: one worker's circuit trip is visible to the others and survives restart."""

from datetime import datetime, timedelta

import redis as redis_lib

from src.stage2.stage2_worker import Stage2Worker
from src.utils.redis import RedisHelper
from src.utils.retry import (
    HTTP_CIRCUIT_BREAKER,
    CircuitBreaker,
    RedisCircuitStore,
    circuit_breaker_sharing_enabled,
)


def _store(client):
    helper = RedisHelper()
    helper._client = client
    return helper, RedisCircuitStore(helper)


def _breaker(store, name="stage2:down.example", host="down.example", **kwargs):
    params = dict(failure_threshold=2, recovery_timeout=60, name=name, host=host, store=store)
    params.update(kwargs)
    return CircuitBreaker(**params)


def test_sharing_defaults_on_and_tests_keep_globals_local(monkeypatch):
    monkeypatch.delenv("CIRCUIT_BREAKER_SHARED", raising=False)
    assert circuit_breaker_sharing_enabled() is True
    monkeypatch.setenv("CIRCUIT_BREAKER_SHARED", "0")
    assert circuit_breaker_sharing_enabled() is False
    assert HTTP_CIRCUIT_BREAKER._store is None


def test_open_on_one_worker_is_visible_to_another_immediately(redis_clean):
    helper, store_a = _store(redis_clean)
    store_b = RedisCircuitStore(helper)
    first = _breaker(store_a)
    second = _breaker(store_b)

    first.record_failure()
    assert first.state == "closed"
    second.record_failure()

    assert second.state == "open"
    assert second.failure_count == 2
    # No local cache: the other worker sees the trip on its next check.
    assert first.can_execute() is False
    assert first.state == "open"

    rows = helper.list_shared_circuits()
    assert len(rows) == 1
    assert rows[0]["name"] == "stage2:down.example"
    assert rows[0]["host"] == "down.example"
    assert rows[0]["state"] == "open"
    state_keys = list(redis_clean.keys("cbstate:*"))
    assert len(state_keys) == 1
    assert redis_clean.ttl(state_keys[0]) > 0


def test_stale_local_clock_does_not_ignore_a_peer_trip(redis_clean):
    helper, store = _store(redis_clean)
    opener = _breaker(store)
    opener.record_failure()
    opener.record_failure()

    local = _breaker(RedisCircuitStore(helper))
    local.state = "closed"
    local.last_failure_time = datetime.now() - timedelta(seconds=3600)
    assert local.can_execute() is False
    assert local.state == "open"


def test_open_state_survives_a_new_process(redis_clean):
    helper, store = _store(redis_clean)
    opener = _breaker(store)
    opener.record_failure()
    opener.record_failure()
    assert opener.state == "open"

    restarted = _breaker(RedisCircuitStore(helper))
    assert restarted.state == "closed"  # memory is empty until the first check
    assert restarted.can_execute() is False
    assert restarted.state == "open"
    assert restarted.failure_count == 2


def test_success_on_any_replica_resets_the_shared_count(redis_clean):
    helper, store_a = _store(redis_clean)
    failing = _breaker(store_a, failure_threshold=3)
    failing.record_failure()
    _breaker(RedisCircuitStore(helper), failure_threshold=3).record_success()

    restarted = _breaker(RedisCircuitStore(helper), failure_threshold=3)
    restarted.record_failure()
    assert restarted.failure_count == 1
    assert restarted.state == "closed"


def test_close_after_recovery_drops_the_shared_key(redis_clean):
    helper, store = _store(redis_clean)
    owner = _breaker(store, recovery_timeout=60)
    owner.record_failure()
    owner.record_failure()
    owner.last_failure_time = datetime.now() - timedelta(seconds=120)
    assert owner.can_execute() is True
    assert owner.state == "half-open"
    owner.record_success()
    owner.record_success()
    assert owner.state == "closed"
    assert helper.list_shared_circuits() == []

    restarted = _breaker(RedisCircuitStore(helper))
    assert restarted.can_execute() is True
    assert restarted.state == "closed"


def test_redis_outage_keeps_the_local_breaker_and_does_not_retry_every_call():
    class _Down:
        @property
        def client(self):
            raise redis_lib.ConnectionError("refused")

    store = RedisCircuitStore(_Down())
    breaker = _breaker(store, failure_threshold=1, recovery_timeout=30)
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.can_execute() is False
    # Cooldown: a second check must not be another connect attempt.
    calls = {"n": 0}

    class _Counting(_Down):
        @property
        def client(self):
            calls["n"] += 1
            raise redis_lib.ConnectionError("refused")

    store._helper = _Counting()
    assert breaker.can_execute() is False
    assert calls["n"] == 0


def test_stage2_breaker_is_keyed_by_host():
    worker = Stage2Worker.__new__(Stage2Worker)
    worker._host_breakers = {}
    worker.breaker_failures = 2
    worker.breaker_recovery = 30
    breaker = worker._breaker("down.example")
    assert breaker.name == "stage2:down.example"
    assert breaker.host == "down.example"
    assert breaker._store is None
