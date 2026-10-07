"""#184: Redis AUTH wired through env/secret; compose binds Redis to localhost."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from src.utils.redis import RedisHelper

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"


def test_password_from_env(monkeypatch):
    monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
    assert RedisHelper(host="h").password == "s3cret"


def test_explicit_password_wins(monkeypatch):
    monkeypatch.setenv("REDIS_PASSWORD", "from-env")
    assert RedisHelper(host="h", password="explicit").password == "explicit"


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_means_no_auth(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("REDIS_PASSWORD", value)
    assert RedisHelper(host="h").password is None


def test_password_is_passed_to_client(monkeypatch):
    seen = {}

    class FakeRedis:
        def __init__(self, **kw):
            seen.update(kw)

        def ping(self):
            return True

    import src.utils.redis as r

    monkeypatch.setattr(r.redis, "Redis", FakeRedis)
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    RedisHelper(host="h").client
    assert seen["password"] == "pw"


def _compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def test_compose_redis_not_exposed_and_auth_capable():
    redis = _compose()["redis"]
    assert all(str(p).startswith("127.0.0.1:") for p in redis.get("ports", []))
    assert "--requirepass" in redis["command"] and "REDIS_PASSWORD" in redis["command"]
    assert any(str(e).startswith("REDIS_PASSWORD=") for e in redis["environment"])


def test_compose_app_services_pass_redis_password():
    for name, svc in _compose().items():
        env = [str(e) for e in svc.get("environment", []) or []]
        if any(e.startswith("REDIS_HOST=") for e in env):
            assert any(e.startswith("REDIS_PASSWORD=") for e in env), name


def test_helm_defaults_require_auth():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["redis"]["auth"]["enabled"] is True
    tpl = (CHART / "templates" / "redis-statefulset.yaml").read_text()
    assert "--requirepass" in tpl and "secretKeyRef" in tpl
    for f in ["scrapy-deployment.yaml", "metrics-exporter-deployment.yaml",
              "stage-workers-deployments.yaml", "exporters-deployments.yaml"]:
        assert ".Values.redis.auth.existingSecret" in (CHART / "templates" / f).read_text(), f


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_helm_render_fails_without_redis_password():
    base = ["helm", "template", "t", str(CHART), "--set", "secrets.useExternalSecrets=false",
            "--set", "secrets.postgres.password=Pg-test-1", "--set", "secrets.grafana.adminPassword=Gf-test-1"]
    bad = subprocess.run(base, capture_output=True, text=True)
    assert bad.returncode != 0 and "Redis AUTH" in bad.stderr
    ok = subprocess.run(base + ["--set", "secrets.redis.password=Rd-test-1"], capture_output=True, text=True)
    assert ok.returncode == 0 and "--requirepass" in ok.stdout
