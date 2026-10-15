"""Hashed Control Center API keys: mint/revoke, 401 on control routes (#658)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cc():
    return _load("dashboard_cc_auth", DASH / "cc_auth.py")


@pytest.fixture(scope="module")
def serve(cc):  # noqa: ARG001 - cc must load first so serve reuses dashboard_cc_auth
    return _load("_cc_serve_658", DASH / "serve.py")


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.kv: dict[str, str] = {}

    def hset(self, name, key=None, value=None, mapping=None):
        bucket = self.hashes.setdefault(name, {})
        if mapping:
            bucket.update(mapping)
        if key is not None:
            bucket[key] = value

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    def get(self, name):
        return self.kv.get(name)

    def set(self, name, value):
        self.kv[name] = value
        return True

    def blob(self) -> str:
        return repr(self.hashes) + repr(self.kv)


class FakePG:
    """Just enough of the DB-API for the three cc_auth statements."""

    def __init__(self):
        self.keys: dict[str, tuple] = {}
        self.by_hash: dict[str, str] = {}
        self.revoked: dict[str, str] = {}
        self.params: list[tuple] = []

    def connect(self):
        return _Conn(self)

    def blob(self) -> str:
        return repr(self.keys) + repr(self.revoked) + repr(self.params)


class _Conn:
    def __init__(self, db: FakePG):
        self.db = db

    def cursor(self):
        return _Cur(self.db)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _Cur:
    def __init__(self, db: FakePG):
        self.db = db
        self._row = None

    def execute(self, sql, params=None):
        self.db.params.append((sql, params))
        compact = " ".join(sql.split())
        if compact.startswith("INSERT INTO cc_api_keys"):
            key_id, name, prefix, token_hash, created_at = params
            self.db.keys[key_id] = (key_id, name, prefix, token_hash, created_at)
            self.db.by_hash[token_hash] = key_id
            self._row = None
        elif compact.startswith("SELECT key_id FROM cc_api_keys"):
            key_id = params[0]
            self._row = (key_id,) if key_id in self.db.keys else None
        elif compact.startswith("INSERT INTO cc_api_key_revocations"):
            key_id, revoked_at = params
            self.db.revoked.setdefault(key_id, revoked_at)
            self._row = None
        elif compact.startswith("SELECT k.key_id"):
            key_id = self.db.by_hash.get(params[0])
            if key_id is None:
                self._row = None
            else:
                self._row = (*self.db.keys[key_id], self.db.revoked.get(key_id))
        else:
            raise AssertionError(compact)

    def fetchone(self):
        return self._row

    def close(self):
        return None


def _directory(cc, redis=None, pg=None):
    stores = []
    if redis is not None:
        stores.append(cc.RedisKeyStore(redis))
    if pg is not None:
        stores.append(cc.PostgresKeyStore(pg.connect))
    return cc.ApiKeyDirectory(stores)


def _assert_hash_only(cc, issued, blob: str):
    assert issued.token not in blob
    assert issued.token_hash == hashlib.sha256(issued.token.encode()).hexdigest()
    assert issued.token_hash in blob
    assert issued.token not in repr(issued)
    assert cc.bearer_token(f"Bearer {issued.token}") == issued.token


def test_redis_mint_revoke_stores_hash_only(cc):
    redis = FakeRedis()
    directory = _directory(cc, redis=redis)
    issued = directory.mint("ops")
    _assert_hash_only(cc, issued, redis.blob())
    assert directory.authenticate(issued.token).key_id == issued.key_id
    assert directory.authenticate(issued.token + "x") is None
    directory.revoke(issued.key_id)
    directory.revoke(issued.key_id)  # idempotent
    assert directory.authenticate(issued.token) is None
    assert issued.token not in redis.blob()


def test_postgres_mint_revoke_stores_hash_only(cc):
    pg = FakePG()
    directory = _directory(cc, pg=pg)
    issued = directory.mint("ops")
    _assert_hash_only(cc, issued, pg.blob())
    for _sql, params in pg.params:
        assert issued.token not in params
    assert directory.authenticate(issued.token).name == "ops"
    directory.revoke(issued.key_id)
    assert issued.key_id in pg.revoked
    assert directory.authenticate(issued.token) is None
    assert "UPDATE" not in pg.blob()


def test_both_backends_fail_closed_when_one_write_fails(cc, caplog):
    caplog.set_level(logging.INFO, logger="dashboard.cc_auth")
    redis = FakeRedis()

    class Boom:
        def save(self, issued):
            raise RuntimeError("down " + issued.token)

        def revoke(self, _key_id, _when):
            raise AssertionError("the store that failed the write is not compensated")

    directory = cc.ApiKeyDirectory([cc.RedisKeyStore(redis), Boom()])
    with pytest.raises(cc.KeyStoreError) as caught:
        directory.mint("ops")
    assert str(caught.value) == "RuntimeError"
    assert cc._TOKEN_IN_TEXT.search(caplog.text) is None
    assert redis.hashes
    for fields in redis.hashes.values():
        assert fields.get("revoked_at")
        assert "cc_" not in "".join(fields.values())


def test_logs_scrub_plaintext(cc, caplog):
    caplog.set_level(logging.INFO, logger="dashboard.cc_auth")
    issued = _directory(cc, redis=FakeRedis()).mint("ops")
    cc.logger.info("leak %s", issued.token)
    try:
        raise RuntimeError(issued.token)
    except RuntimeError:
        cc.logger.exception("control key failed")
    assert issued.token not in caplog.text
    assert "[REDACTED]" in caplog.text
    assert "cc_api_key minted" in caplog.text


def test_cli_prints_secret_once_and_revoke_rejects_the_secret(cc, monkeypatch, capsys, caplog):
    caplog.set_level(logging.INFO, logger="dashboard.cc_auth")
    redis = FakeRedis()
    monkeypatch.setattr(cc, "store_from_env", lambda env=None: _directory(cc, redis=redis))
    assert cc.main(["mint", "--name", "ops"]) == 0
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    token = lines[1]
    key_id = lines[0].split()[0].split("=", 1)[1]
    assert token.startswith("cc_") and out.count(token) == 1
    assert token not in caplog.text
    assert cc.main(["revoke", token]) == 2
    err = capsys.readouterr().err
    assert token not in err
    assert "not the API key" in err
    assert cc.main(["revoke", key_id]) == 0
    assert _directory(cc, redis=redis).authenticate(token) is None
    missing = "ck_" + "ab" * 8
    assert cc.main(["revoke", missing]) == 1
    assert cc.main(["mint", "--name", "has space"]) == 2


def test_redis_settings_ignore_redis_url(cc):
    kwargs = cc.redis_connection_kwargs({
        "REDIS_URL": "redis://:s3cret@evil.example:9/4",
        "REDIS_HOST": "cache",
        "REDIS_PORT": "6380",
        "REDIS_DB": "2",
        "REDIS_PASSWORD": "pw",
    })
    assert kwargs["host"] == "cache"
    assert kwargs["port"] == 6380
    assert kwargs["db"] == 2
    assert kwargs["password"] == "pw"
    assert "evil.example" not in repr(kwargs)
    assert "s3cret" not in repr(kwargs)
    with pytest.raises(cc.KeyStoreError):
        cc.redis_connection_kwargs({"REDIS_PORT": "nope"})
    with pytest.raises(cc.KeyStoreError):
        cc.store_from_env({"CC_API_KEY_BACKEND": "file"})


def test_store_from_env_does_not_connect(cc, monkeypatch):
    def boom(_env):
        raise AssertionError("connected")

    monkeypatch.setattr(cc, "redis_client_from_env", boom)
    directory = cc.store_from_env({"CC_API_KEY_BACKEND": "redis", "REDIS_HOST": "cache"})
    assert isinstance(directory, cc.ApiKeyDirectory)


def test_init_sql_adds_hash_tables_without_widening_grants():
    docker = (ROOT / "docker" / "init-db.sql").read_text()
    helm = (ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "init-db.sql").read_text()
    assert docker == helm
    assert "CREATE TABLE IF NOT EXISTS cc_api_keys (" in docker
    assert "token_hash TEXT NOT NULL UNIQUE" in docker
    assert "CREATE TABLE IF NOT EXISTS cc_api_key_revocations (" in docker
    assert "cc_api_keys, cc_api_key_revocations" in docker
    assert "GRANT UPDATE" not in docker
    assert "token TEXT" not in docker


def _request(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


@pytest.fixture
def control_server(serve, cc, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    servers = []

    def start(store, env=None, runner=None):
        env = {"CC_RATE_LIMIT": "0"} if env is None else env
        logs: list = []
        attrs = {
            "root": root,
            "log_message": lambda *args: logs.append(args),
            "auth": serve.AuthGate.from_env(env),
            "limiter": serve.RateLimiter.from_env(env),
            "cors_origins": serve.parse_cors_origins(env),
            "csp": serve.build_csp(env),
            "metrics_upstream": serve.DEFAULT_METRICS_UPSTREAM,
            "upstream_timeout": 4.0,
            "queue_keys": serve.queue_keys(env),
            "api_key_store": store,
            "control_runner": None,
        }
        handler = type("H", (serve.DashboardHandler,), attrs)
        if runner is not None:
            handler.control_runner = staticmethod(runner)
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        return base, logs

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def test_control_routes_require_a_key_and_do_not_log_it(cc, control_server, caplog):
    caplog.set_level(logging.INFO, logger="dashboard.serve")
    redis = FakeRedis()
    directory = _directory(cc, redis=redis)
    issued = directory.mint("ops")
    called: list[tuple[str, str]] = []

    def runner(action, key_id):
        called.append((action, key_id))

    env = {"CC_RATE_LIMIT": "0", "CC_AUTH_TOKEN": "shared-secret"}
    base, logs = control_server(directory, env=env, runner=runner)
    status, headers, body = _request(base + "/api/control/start", method="POST")
    assert status == 401 and b"unauthorized" in body
    assert headers["www-authenticate"].startswith("Bearer ")
    assert called == []

    shared = {"Authorization": "Bearer shared-secret"}
    assert _request(base + "/api/control/status", headers=shared)[0] == 401
    basic = "Basic b3BzOnNoYXJlZC1zZWNyZXQ="  # ops:shared-secret
    assert _request(base + "/api/control/status", headers={"Authorization": basic})[0] == 401
    assert called == []

    bad = {"Authorization": f"Bearer {issued.token}nope"}
    status, _, body = _request(base + "/api/control/status", headers=bad)
    assert status == 401 and issued.token.encode() not in body

    good = {"Authorization": f"Bearer {issued.token}"}
    status, _, body = _request(base + "/api/control/status", headers=good)
    payload = json.loads(body)
    assert status == 200 and payload == {"ok": True, "key_id": issued.key_id, "name": "ops"}
    assert issued.token.encode() not in body

    status, _, body = _request(base + "/api/control/start", headers=good, method="POST")
    assert status == 200 and json.loads(body)["action"] == "start"
    assert called == [("start", issued.key_id)]
    assert issued.token not in repr(logs)
    assert issued.token not in caplog.text

    directory.revoke(issued.key_id)
    called.clear()
    assert _request(base + "/api/control/stop", headers=good, method="POST")[0] == 401
    assert called == []


def test_unconfigured_start_is_501_and_store_outage_is_503(cc, control_server):
    redis = FakeRedis()
    directory = _directory(cc, redis=redis)
    issued = directory.mint("ops")
    base, _logs = control_server(directory)
    good = {"Authorization": f"Bearer {issued.token}"}
    assert _request(base + "/")[0] == 200  # dashboard gate stays off on loopback
    status, _, body = _request(base + "/api/control/start", headers=good, method="POST")
    assert status == 501 and json.loads(body)["error"] == "control action not configured"
    assert issued.token.encode() not in body
    status, _, body = _request(base + "/api/control/missing", headers=good)
    assert status == 404

    class Down:
        def authenticate(self, _token):
            raise cc.KeyStoreError("ConnectionError")

    base, logs = control_server(Down())
    shaped = "cc_" + ("a" * 43)
    status, _, body = _request(base + "/api/control/status", headers={"Authorization": f"Bearer {shaped}"})
    assert status == 503 and b"key store unavailable" in body
    assert shaped.encode() not in body
    assert shaped not in repr(logs)

    def explode(_action, _key_id):
        raise RuntimeError("boom " + issued.token)

    base, logs = control_server(directory, runner=explode)
    status, _, body = _request(base + "/api/control/stop", headers=good, method="POST")
    assert status == 500 and b"control action failed" in body
    assert issued.token.encode() not in body
    assert issued.token not in repr(logs)


def test_control_route_is_rate_limited(cc, control_server):
    directory = _directory(cc, redis=FakeRedis())
    base, _logs = control_server(directory, env={"CC_RATE_LIMIT": "60", "CC_RATE_BURST": "1"})
    assert _request(base + "/api/control/status")[0] == 401
    assert _request(base + "/api/control/status")[0] == 429


def test_request_path_collapses_dotdot(serve):
    assert serve.request_path("/api/control/../index.html") == "/api/index.html"
    assert serve.request_path("/api/control/../../etc/passwd") == "/etc/passwd"
    assert serve.request_path("/api/../api/control/status") == "/api/control/status"
    assert serve.is_control_route("/api/control/status")
    assert serve.is_control_route("/api/../api/control/status?x=1")
    assert not serve.is_control_route("/api/control/../index.html")
