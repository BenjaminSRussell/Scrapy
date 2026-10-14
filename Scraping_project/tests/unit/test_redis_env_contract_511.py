"""One Redis env contract across Python, Rust and Helm (#511)."""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.utils import redis_env
from src.utils.redis_env import redis_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_warnings():
    redis_env._warned.clear()


def test_defaults():
    s = redis_settings({})
    assert (s.host, s.port, s.password, s.db) == ("localhost", 6379, None, 0)
    assert s.url == "redis://localhost:6379/0"


def test_canonical_variables():
    s = redis_settings({"REDIS_HOST": "redis", "REDIS_PORT": "6380", "REDIS_PASSWORD": "p@ss:w/rd", "REDIS_DB": "2"})
    assert (s.host, s.port, s.password, s.db) == ("redis", 6380, "p@ss:w/rd", 2)
    assert s.url == "redis://:p%40ss%3Aw%2Frd@redis:6380/2"


def test_redis_url_is_fallback_when_host_unset():
    s = redis_settings({"REDIS_URL": "redis://:secret@cache:6390/3"})
    assert (s.host, s.port, s.password, s.db) == ("cache", 6390, "secret", 3)


def test_blank_values_count_as_unset():
    s = redis_settings({"REDIS_HOST": "  ", "REDIS_PORT": "", "REDIS_URL": "redis://cache:1"})
    assert (s.host, s.port) == ("cache", 1)


def test_host_port_win_over_conflicting_url_and_warn_once(caplog):
    env = {"REDIS_HOST": "redis", "REDIS_URL": "redis://elsewhere:1234"}
    with caplog.at_level(logging.WARNING, logger="src.utils.redis_env"):
        s1 = redis_settings(env)
        s2 = redis_settings(env)
    assert (s1.host, s1.port) == (s2.host, s2.port) == ("redis", 6379)
    assert len([r for r in caplog.records if "disagrees" in r.getMessage()]) == 1


def test_agreeing_url_does_not_warn_and_fills_password(caplog):
    env = {"REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_URL": "redis://:pw@redis:6379"}
    with caplog.at_level(logging.WARNING, logger="src.utils.redis_env"):
        s = redis_settings(env)
    assert s.password == "pw"
    assert not caplog.records


def test_bad_scheme_rejected():
    with pytest.raises(ValueError, match="redis://"):
        redis_settings({"REDIS_URL": "http://redis:6379"})


def test_redis_helper_uses_contract(monkeypatch):
    from src.utils.redis import RedisHelper

    for k in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://:pw@cache:6391/4")
    h = RedisHelper()
    assert (h.host, h.port, h.password, h.db) == ("cache", 6391, "pw", 4)
    explicit = RedisHelper(host="other", port=1, password="x")
    assert (explicit.host, explicit.port, explicit.password) == ("other", 1, "x")


def test_probe_uses_contract(monkeypatch):
    from src.utils import probe

    seen = {}

    class Boom(OSError):
        pass

    def fake_conn(addr, timeout):
        seen["addr"] = addr
        raise Boom()

    for k in ("REDIS_HOST", "REDIS_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://cache:6392")
    monkeypatch.setattr(probe.socket, "create_connection", fake_conn)
    assert probe.redis_ready(timeout=0.1) is False
    assert seen["addr"] == ("cache", 6392)


def test_rust_ingest_reads_same_contract():
    src = (ROOT / "kafka-delta-ingest" / "src" / "main.rs").read_text()
    assert 'std::env::var("REDIS_URL").unwrap_or_else' not in src
    assert "redis_url_from_env(|k| std::env::var(k).ok())" in src
    for var in ("REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB", "REDIS_URL"):
        assert f'"{var}"' in src


def test_compose_sets_only_host_port_password():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "REDIS_URL" not in compose
    assert compose.count("REDIS_HOST=redis") >= 1


def _helm():
    for cand in (shutil.which("helm"), "/tmp/helm"):
        if cand and Path(cand).exists():
            return cand
    return None


@pytest.mark.skipif(_helm() is None, reason="helm not installed")
def test_helm_drops_redis_url_and_gives_ingestor_the_redis_secret():
    chart = ROOT / "k8s" / "helm" / "scraping-pipeline"
    out = subprocess.run(
        [
            _helm(),
            "template",
            "t",
            str(chart),
            "-s",
            "templates/application-configmap.yaml",
            "-s",
            "templates/kafka-delta-ingestor-deployment.yaml",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert not re.search(r"^\s*REDIS_URL:", out, re.M)
    assert re.search(r"^\s*REDIS_HOST:", out, re.M)
    ingestor = out[out.index("kind: Deployment") :]
    assert "name: redis-credentials" in ingestor


def test_helm_templates_have_no_redis_url_value():
    tpl = (ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates" / "application-configmap.yaml").read_text()
    assert not re.search(r"^\s*REDIS_URL:", tpl, re.M)
