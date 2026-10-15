"""Disabled OIDC settings: save fields, login stays not_implemented (#673)."""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.utils import oidc_placeholder
from src.utils.oidc_placeholder import NOT_IMPLEMENTED, OidcConfigError, OidcConfigStore, oidc_login

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"
SHIPPED = ROOT / "config.yml"


def _fields(**overrides: str) -> dict:
    body = {
        "issuer": "https://login.example.com/realms/uconn",
        "client_id": "control-center",
        "redirect_uri": "http://127.0.0.1:8080/auth/callback",
        "scopes": "openid profile email",
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_login_is_not_implemented_and_ignores_saved_fields(tmp_path: Path) -> None:
    store = OidcConfigStore(path=tmp_path / "oidc.json", defaults_path=tmp_path / "missing.yml")
    saved = store.save(_fields(client_secret="super-secret"))
    assert oidc_login(saved) == NOT_IMPLEMENTED
    assert store.login_response() == {
        "ok": False,
        "status": NOT_IMPLEMENTED,
        "error": NOT_IMPLEMENTED,
        "enabled": False,
    }
    assert "issuer" not in store.login_response()
    assert saved["enabled"] is False
    assert saved["login"] == NOT_IMPLEMENTED
    assert saved["client_secret_set"] is True
    assert "client_secret" not in saved
    assert "super-secret" not in json.dumps(saved)


def test_secret_is_kept_redacted_and_cleared(tmp_path: Path) -> None:
    path = tmp_path / "oidc.json"
    store = OidcConfigStore(path=path, defaults_path=tmp_path / "missing.yml")
    store.save(_fields(client_secret="super-secret"))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["client_secret"] == "super-secret"
    assert on_disk["enabled"] is False
    assert (path.stat().st_mode & 0o777) == 0o600

    kept = store.save(_fields())
    assert kept["client_secret_set"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["client_secret"] == "super-secret"

    cleared = store.save(_fields(client_secret=""))
    assert cleared["client_secret_set"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["client_secret"] == ""


def test_enabled_true_and_bad_fields_do_not_enable(tmp_path: Path) -> None:
    store = OidcConfigStore(path=None, defaults_path=tmp_path / "missing.yml")
    view = store.save(_fields(enabled=True))
    assert view["enabled"] is False
    assert store.login_response()["status"] == NOT_IMPLEMENTED

    with pytest.raises(OidcConfigError) as unknown:
        store.save({**_fields(), "issuer_url": "https://idp.example"})
    assert unknown.value.field == "issuer_url"
    assert "https://idp.example" not in str(unknown.value)

    with pytest.raises(OidcConfigError) as secret:
        store.save(_fields(client_secret="x" * 513))
    assert secret.value.field == "client_secret"
    assert "x" * 20 not in str(secret.value)

    for bad in ("javascript:alert(1)", "https://user:pass@idp.example/realms/a", "https://idp.example/a b"):
        with pytest.raises(OidcConfigError):
            store.save(_fields(issuer=bad))
    with pytest.raises(OidcConfigError):
        store.save(_fields(scopes="openid <script>"))
    with pytest.raises(OidcConfigError):
        store.save(_fields(enabled="true"))


def test_overlay_beats_shipped_strings_and_corrupt_file_falls_back(tmp_path: Path) -> None:
    defaults = tmp_path / "config.yml"
    defaults.write_text(
        "oidc:\n  enabled: true\n  issuer: https://from-yaml.example\n  client_secret: should-not-load\n",
        encoding="utf-8",
    )
    store = OidcConfigStore(path=tmp_path / "missing.json", defaults_path=defaults)
    loaded = store.public()
    assert loaded["issuer"] == "https://from-yaml.example"
    assert loaded["enabled"] is False
    assert loaded["client_secret_set"] is False

    path = tmp_path / "oidc.json"
    path.write_text("{", encoding="utf-8")
    broken = OidcConfigStore(path=path, defaults_path=defaults)
    assert broken.public()["issuer"] == "https://from-yaml.example"
    broken.save(_fields(issuer="https://saved.example"))
    assert broken.public()["issuer"] == "https://saved.example"


def test_shipped_config_is_disabled_and_has_no_secret(tmp_path: Path) -> None:
    import yaml

    section = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))["oidc"]
    assert section["enabled"] is False
    assert "client_secret" not in section
    assert section["scopes"] == "openid profile email"
    store = OidcConfigStore(path=tmp_path / "missing.json", defaults_path=SHIPPED)
    assert store.public()["enabled"] is False
    assert store.public()["login"] == NOT_IMPLEMENTED
    assert store.public()["issuer"] == ""


def test_empty_env_path_is_memory_only(tmp_path: Path) -> None:
    overlay = oidc_placeholder._DEFAULT_OVERLAY
    existed = overlay.exists()
    default = OidcConfigStore.from_env({}, defaults_path=tmp_path / "no.yml")
    assert default.path == overlay
    assert overlay.exists() is existed
    memory = OidcConfigStore.from_env({"CC_OIDC_CONFIG_PATH": ""}, defaults_path=tmp_path / "no.yml")
    assert memory.path is None
    memory.save(_fields(client_secret="only-memory"))
    assert memory.public()["client_secret_set"] is True


def _load_serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_oidc", DASH / "serve.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def oidc_server(tmp_path: Path):
    serve = _load_serve()
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>", encoding="utf-8")
    store = OidcConfigStore(path=tmp_path / "oidc.json", defaults_path=tmp_path / "missing.yml")

    def start(env: dict[str, str]):
        handler = type(
            "H",
            (serve.DashboardHandler,),
            {
                "root": root,
                "log_message": lambda *a: None,
                "auth": serve.AuthGate.from_env(env),
                "limiter": serve.RateLimiter.from_env(env),
                "cors_origins": serve.parse_cors_origins(env),
                "csp": serve.build_csp(env),
                "metrics_upstream": "http://127.0.0.1:9/metrics",
                "upstream_timeout": 0.2,
                "queue_keys": (),
                "oidc_store": store,
            },
        )
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{httpd.server_address[1]}", httpd

    servers = []

    def launch(env: dict[str, str]):
        base, httpd = start(env)
        servers.append(httpd)
        return base

    yield launch
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _request(url: str, *, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_save_and_login_path(oidc_server) -> None:
    base = oidc_server({})
    secret = "s3cret-value"
    payload = json.dumps(_fields(client_secret=secret)).encode()
    status, raw = _request(
        base + "/api/sso/oidc",
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    saved = json.loads(raw)
    assert saved["saved"] is True
    assert saved["enabled"] is False
    assert saved["login"] == NOT_IMPLEMENTED
    assert secret.encode() not in raw

    status, raw = _request(base + "/api/sso/oidc")
    assert status == 200
    assert secret.encode() not in raw
    assert json.loads(raw)["client_secret_set"] is True

    status, raw = _request(base + "/api/sso/oidc/login")
    assert status == 501
    assert json.loads(raw)["status"] == NOT_IMPLEMENTED
    status, raw = _request(base + "/api/sso/oidc/login", method="POST", body=b"{}", headers={"Content-Type": "application/json"})
    assert status == 501
    assert json.loads(raw)["error"] == NOT_IMPLEMENTED


def test_http_login_is_public_and_save_stays_gated(oidc_server) -> None:
    base = oidc_server({"CC_BASIC_AUTH": "ops:s3cret"})
    status, raw = _request(base + "/api/sso/oidc/login")
    assert status == 501
    assert json.loads(raw)["status"] == NOT_IMPLEMENTED
    status, raw = _request(
        base + "/api/sso/oidc",
        method="POST",
        body=json.dumps(_fields()).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 401
    assert b"not_implemented" not in raw
