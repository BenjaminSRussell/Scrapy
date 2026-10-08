"""#472: SmartCache stores JSON in Redis and never unpickles Redis values."""

import ast
import pickle
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils.cache import SmartCache

CACHE_SRC = Path(__file__).resolve().parents[2] / "src" / "utils" / "cache.py"
VALUES = [{"a": 1, "nested": {"b": [1, 2.5, None, True]}}, [1, "two", {"three": 3}], "plain string", 42]


class DictRedis:
    """Minimal str-valued Redis stand-in (the real client uses decode_responses=True)."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        assert isinstance(v, str), "cache must write text (JSON), not bytes"
        self.data[k] = v

    def setex(self, k, ttl, v):
        self.set(k, v)

    def delete(self, *ks):
        for k in ks:
            self.data.pop(k, None)

    def keys(self, pattern):
        return [k for k in self.data if k.startswith(pattern.rstrip("*"))]


def _cache(client):
    return SmartCache(SimpleNamespace(client=client))


def test_cache_module_does_not_import_pickle():
    tree = ast.parse(CACHE_SRC.read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"pickle", "cPickle", "dill", "marshal", "shelve"}


@pytest.mark.parametrize("value", VALUES)
def test_l2_round_trip_is_json(value):
    client = DictRedis()
    cache = _cache(client)
    assert cache.set("k", value, ttl=60)
    cache.local_cache.clear()  # force the Redis (L2) path
    assert cache.get("k") == value
    stored = client.data["cache:k"]
    assert __import__("json").loads(stored) == value


class Boom:
    triggered = False

    def __reduce__(self):
        return (Boom._trigger, ())

    @staticmethod
    def _trigger():
        Boom.triggered = True
        return "pwned"


@pytest.mark.parametrize("payload", [pickle.dumps(Boom()), pickle.dumps(Boom()).decode("latin-1")])
def test_pickle_payload_in_redis_is_never_executed(payload, caplog):
    client = DictRedis()
    client.data["cache:evil"] = payload
    cache = _cache(client)
    Boom.triggered = False
    assert cache.get("evil") is None
    assert Boom.triggered is False
    assert cache.cache_stats["misses"] == 1


@pytest.fixture
def live_redis():
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server not installed")
    redis = pytest.importorskip("redis")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([binary, "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
    for _ in range(50):
        try:
            client.ping()
            break
        except Exception:
            time.sleep(0.1)
    try:
        yield client
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("value", VALUES)
def test_live_redis_round_trip(live_redis, value):
    cache = _cache(live_redis)
    assert cache.set("live", value, ttl=30)
    fresh = _cache(live_redis)  # new process view: empty L1
    assert fresh.get("live") == value
    assert 0 < live_redis.ttl("cache:live") <= 30
