"""#161: seen-URL sets must never be silently LRU-evicted.

Compose/Helm ran Redis with ``allkeys-lru``. seen:* sets and queues have no
TTL, so at maxmemory Redis evicted them and the crawl started over. Now the
policy is ``volatile-lru`` (only TTL keys are evictable), the client logs an
ERROR on an allkeys-* server, and RedisHighMemory warns at 80%.
"""
from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from src.utils.redis import RedisHelper, check_eviction_policy

ROOT = Path(__file__).resolve().parents[2]


class FakeClient:
    def __init__(self, policy=None, exc=None):
        self.policy, self.exc = policy, exc

    def config_get(self, name):
        if self.exc:
            raise self.exc
        return {name: self.policy}


@pytest.mark.parametrize("policy", ["allkeys-lru", "allkeys-lfu", "allkeys-random"])
def test_allkeys_policies_log_an_error(policy, caplog):
    with caplog.at_level(logging.ERROR, logger="src.utils.redis"):
        assert check_eviction_policy(FakeClient(policy)) == policy
    assert "#161" in caplog.text and policy in caplog.text


@pytest.mark.parametrize("policy", ["volatile-lru", "volatile-ttl", "noeviction"])
def test_safe_policies_are_quiet(policy, caplog):
    with caplog.at_level(logging.ERROR, logger="src.utils.redis"):
        assert check_eviction_policy(FakeClient(policy)) == policy
    assert caplog.text == ""


def test_config_unavailable_is_not_fatal():
    assert check_eviction_policy(FakeClient(exc=Exception("unknown command 'CONFIG'"))) is None


def test_compose_k8s_and_helm_use_volatile_lru():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "--maxmemory-policy volatile-lru" in compose
    assert "allkeys-" not in compose
    values = yaml.safe_load((ROOT / "k8s/helm/scraping-pipeline/values.yaml").read_text())
    assert values["redis"]["config"]["maxmemoryPolicy"] == "volatile-lru"
    assert "allkeys-" not in (ROOT / "k8s/deployment.yaml").read_text()


@pytest.mark.parametrize("rel", ["monitoring/alerting_rules.yml",
                                 "k8s/helm/scraping-pipeline/files/monitoring/alerting_rules.yml"])
def test_memory_alert_fires_at_80_percent(rel):
    rules = [r for g in yaml.safe_load((ROOT / rel).read_text())["groups"] for r in g["rules"]]
    alert = next(r for r in rules if r.get("alert") == "RedisHighMemory")
    assert alert["expr"].strip().endswith("> 0.8")
    assert "redis_memory_max_bytes > 0" in alert["expr"]  # maxmemory unset -> no alert


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server not installed")
@pytest.mark.parametrize("policy,survives", [("allkeys-lru", False), ("volatile-lru", True)])
def test_seen_set_under_memory_pressure(policy, survives):
    """Real server at maxmemory: allkeys-lru drops the seen set, volatile-lru keeps it."""
    import redis

    port = _free_port()
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "",
                             "--appendonly", "no", "--maxmemory", "3mb", "--maxmemory-policy", policy],
                            stdout=subprocess.DEVNULL)
    try:
        helper = RedisHelper(host="127.0.0.1", port=port)
        for _ in range(50):
            try:
                helper.client
                break
            except redis.ConnectionError:
                helper._client = None
                time.sleep(0.05)
        for i in range(500):
            helper.mark_url_seen(f"https://uconn.edu/p/{i}")
        rejected = 0
        for i in range(40000):  # half cache-like (TTL), half durable; 3mb fills first
            try:
                helper.client.set(f"filler:{i}", "x" * 200, ex=3600 if i % 2 else None)
            except redis.exceptions.ResponseError:
                rejected += 1
                if rejected > 5:
                    break
        assert helper.check_url_seen("https://uconn.edu/p/0") is survives
        assert (rejected > 0) is survives  # volatile-lru rejects writes instead of evicting
    finally:
        proc.terminate()
        proc.wait()
