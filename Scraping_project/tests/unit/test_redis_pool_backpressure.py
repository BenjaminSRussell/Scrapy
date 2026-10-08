"""#533: Redis pool exhaustion must fail closed, never fan out duplicates.

Runs against a real Redis: CI's service on localhost:6379 or, failing that, a
throwaway ``redis-server`` started here. Skipped only if neither exists.
"""
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid

import pytest
import redis

from src.utils import redis as r
from src.utils.redis import RedisHelper, SeenStoreUnavailable, is_pool_exhausted


def _ping(host, port):
    try:
        return redis.Redis(host=host, port=port, socket_connect_timeout=0.5).ping()
    except Exception:
        return False


@pytest.fixture(scope="module")
def redis_endpoint():
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    if _ping(host, port):
        yield host, port
        return
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("no Redis available")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([binary, "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if _ping("127.0.0.1", port):
                break
            time.sleep(0.1)
        else:
            pytest.skip("redis-server did not start")
        yield "127.0.0.1", port
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def helper(redis_endpoint, monkeypatch):
    monkeypatch.delenv(r.SEEN_FAIL_MODE_ENV, raising=False)
    host, port = redis_endpoint
    h = RedisHelper(host=host, port=port, db=15, max_connections=2, pool_timeout=0.2)
    prefix = f"t533:{uuid.uuid4().hex}"
    yield h, prefix
    h.client.delete(f"{prefix}:urls")


def _exhausted(op):
    try:
        from prometheus_client import REGISTRY
    except Exception:
        return None
    return REGISTRY.get_sample_value("redis_pool_exhausted_total", {"op": op}) or 0.0


def _hold_all_connections(h, seconds):
    """Occupy every pooled connection with a blocking BLPOP."""
    ts = [threading.Thread(target=h.client.blpop, args=(f"t533-block-{uuid.uuid4().hex}", seconds))
          for _ in range(h.max_connections)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    return ts


def test_pool_is_bounded_by_config(helper):
    h, _ = helper
    pool = h.client.connection_pool
    assert isinstance(pool, redis.BlockingConnectionPool)
    assert pool.max_connections == 2 and pool.timeout == 0.2


def test_env_configures_the_pool(monkeypatch):
    monkeypatch.setenv(r.MAX_CONNECTIONS_ENV, "7")
    monkeypatch.setenv(r.POOL_TIMEOUT_ENV, "0.5")
    h = RedisHelper(host="h")
    assert (h.max_connections, h.pool_timeout) == (7, 0.5)
    monkeypatch.setenv(r.MAX_CONNECTIONS_ENV, "nonsense")
    monkeypatch.setenv(r.POOL_TIMEOUT_ENV, "-1")
    h = RedisHelper(host="h")
    assert (h.max_connections, h.pool_timeout) == (r.DEFAULT_MAX_CONNECTIONS, r.DEFAULT_POOL_TIMEOUT)


def test_exhausted_pool_fails_closed_and_is_counted(helper):
    h, prefix = helper
    before = _exhausted("claim")
    holders = _hold_all_connections(h, 2)
    try:
        with pytest.raises(SeenStoreUnavailable) as exc:
            h.claim_urls([f"https://x.example/{i}" for i in range(5)], key_prefix=prefix)
        assert is_pool_exhausted(exc.value.__cause__)
        with pytest.raises(SeenStoreUnavailable):
            h.claim_url("https://x.example/single", key_prefix=prefix)
    finally:
        for t in holders:
            t.join()
    if before is not None:
        assert _exhausted("claim") == before + 2
    # Nothing was claimed while the pool was exhausted.
    assert h.client.scard(f"{prefix}:urls") == 0


def test_burst_under_exhaustion_produces_no_duplicate_storm(helper):
    """32 workers race the same 200 URLs while the pool keeps saturating.
    Every URL is owned by at most one worker. Workers that hit exhaustion
    get SeenStoreUnavailable and admit nothing, instead of re-emitting URLs."""
    h, prefix = helper
    urls = [f"https://uconn.example/page/{i}" for i in range(200)]
    owned: list[list[str]] = []
    paused = []
    lock = threading.Lock()
    barrier = threading.Barrier(32)

    def worker(i):
        barrier.wait()
        for start in range(0, 200, 20):
            try:
                got = h.claim_urls(urls[start:start + 20], key_prefix=prefix)
            except SeenStoreUnavailable:
                with lock:
                    paused.append(i)
                continue
            with lock:
                owned.append(got)

    holders = _hold_all_connections(h, 1)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for t in holders:
        t.join()

    flat = [u for batch in owned for u in batch]
    assert len(flat) == len(set(flat)), "a URL was handed to two workers"
    assert paused, "the burst never hit pool exhaustion; the test isn't exercising #533"
    # Whatever was admitted is exactly what Redis recorded; nothing was "owned" without a claim.
    assert set(flat) == set(h.client.smembers(f"{prefix}:urls"))


def test_fail_open_mode_shows_the_hazard_this_guards_against(helper, monkeypatch):
    """With the explicit debug opt-out, exhaustion is reported as 'claimed' for
    every caller. That's the duplicate storm the default policy prevents."""
    h, prefix = helper
    monkeypatch.setenv(r.SEEN_FAIL_MODE_ENV, "open")
    holders = _hold_all_connections(h, 1)
    try:
        a = h.claim_urls(["https://x.example/dup"], key_prefix=prefix)
        b = h.claim_urls(["https://x.example/dup"], key_prefix=prefix)
    finally:
        for t in holders:
            t.join()
    assert a == b == ["https://x.example/dup"]


def test_alerts_exist_in_both_rule_files():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    for rel in ("monitoring/alerting_rules.yml",
                "k8s/helm/scraping-pipeline/files/monitoring/alerting_rules.yml"):
        rules = yaml.safe_load((root / rel).read_text())
        alerts = {x["alert"]: x for g in rules["groups"] for x in g["rules"] if "alert" in x}
        assert "redis_pool_exhausted_total" in alerts["RedisPoolExhausted"]["expr"], rel
        assert "redis_seen_check_errors_total" in alerts["RedisSeenStoreFailingClosed"]["expr"], rel
