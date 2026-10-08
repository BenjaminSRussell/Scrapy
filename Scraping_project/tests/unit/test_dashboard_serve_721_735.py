"""Dashboard serve.py: 404s for missing/non-asset paths (#721) and bind host/port (#735)."""
from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def server(serve, tmp_path):
    """Real server on 127.0.0.1:<ephemeral> over an isolated static root (never a production port)."""
    root = tmp_path / "static"
    (root / "tests").mkdir(parents=True)
    (root / "sub").mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    (root / "app.js").write_text("console.log('ok');")
    (root / "style.css").write_text("body{}")
    (root / "serve.py").write_text("SECRET = 1")
    (root / "README.md").write_text("docs")
    (root / ".env").write_text("TOKEN=x")
    (root / "tests" / "t.js").write_text("test")

    handler = type("H", (serve.DashboardHandler,), {"root": root, "log_message": lambda *a: None})
    httpd = serve.make_server("127.0.0.1", 0, handler=handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode()


@pytest.mark.parametrize("path", ["/missing.css", "/missing.js", "/img/missing.png", "/nope/"])
def test_missing_assets_are_404_without_paths(server, path):
    base, root = server
    status, ctype, body = get(base + path)
    assert status == 404
    assert "text/html" in ctype
    assert str(root) not in body and "/tmp" not in body and "CC</title>" not in body  # not the app shell


@pytest.mark.parametrize("path", ["/serve.py", "/README.md", "/.env", "/tests/t.js", "/sub/", "/tests/"])
def test_source_docs_dotfiles_and_listings_are_not_served(server, path):
    base, _ = server
    status, _, body = get(base + path)
    assert status == 404
    assert "SECRET" not in body and "TOKEN" not in body and "Directory listing" not in body


def test_existing_assets_still_served(server):
    base, _ = server
    assert get(base + "/")[0] == 200 and "CC</title>" in get(base + "/")[2]
    status, ctype, body = get(base + "/app.js")
    assert status == 200 and "javascript" in ctype and "console.log" in body
    assert get(base + "/style.css")[0] == 200
    status, _, body = get(base + "/version.js?x=1")
    assert status == 200 and body.startswith("window.__CC_VERSION__")


def test_default_bind_is_loopback(serve):
    assert serve.resolve_bind(env={}) == ("127.0.0.1", 8080)


def test_bind_overrides(serve):
    assert serve.resolve_bind(env={"CC_HOST": "10.0.0.5", "CC_PORT": "9000"}) == ("10.0.0.5", 9000)
    assert serve.resolve_bind("127.0.0.2", 8181, env={"CC_HOST": "0.0.0.0", "CC_PORT": "1"}) == ("127.0.0.2", 8181)
    for bad in ("http", "-1", "70000"):
        with pytest.raises(SystemExit):
            serve.resolve_bind(port=bad, env={})


def test_make_server_passes_exact_address_and_warns_on_wildcard(serve, capsys):
    calls = []

    def factory(address, handler):  # no real listener
        calls.append((address, handler))
        return object()

    serve.make_server("127.0.0.1", 8080, server_factory=factory)
    assert calls[-1] == (("127.0.0.1", 8080), serve.DashboardHandler)
    assert capsys.readouterr().err == ""
    serve.make_server("0.0.0.0", 8080, server_factory=factory)
    assert calls[-1][0] == ("0.0.0.0", 8080) and "all interfaces" in capsys.readouterr().err


def test_main_uses_resolved_bind_not_wildcard(serve, monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def fake_make_server(host, port, **kw):
        seen["addr"] = (host, port)
        raise Stop

    monkeypatch.delenv("CC_HOST", raising=False)
    monkeypatch.delenv("CC_PORT", raising=False)
    monkeypatch.setattr(serve, "make_server", fake_make_server)
    with pytest.raises(Stop):
        serve.main([])
    assert seen["addr"] == ("127.0.0.1", 8080)
    with pytest.raises(Stop):
        serve.main(["--port", "8099"])
    assert seen["addr"] == ("127.0.0.1", 8099)
