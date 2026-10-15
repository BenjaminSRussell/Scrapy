"""Dashboard serve.py: 404s for missing/non-asset paths (#721) and bind host/port (#735)."""
from __future__ import annotations

import http.client
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


def test_only_allowlisted_extensions_are_served(server):
    """#990: unknown paths and non-allowlisted types are 404; static types still load."""
    base, root = server
    (root / "icon.svg").write_text("<svg id='ok'/>")
    (root / "logo.png").write_bytes(b"PNG-ok")
    (root / "favicon.ico").write_bytes(b"ICO-ok")
    (root / "app.js.map").write_text('{"file":"app.js"}')
    (root / "backup.json").write_text('{"token":"SECRETJSON"}')
    (root / "notes.txt").write_text("SECRETTXT")
    (root / "photo.jpg").write_bytes(b"SECRETJPG")
    (root / "font.woff2").write_bytes(b"SECRETWOFF")
    (root / "dump.sql").write_text("SECRETDUMP")
    (root / "archive.zip").write_bytes(b"SECRETZIP")
    (root / "config.yml").write_text("SECRETYML")
    (root / "secret.bak").write_text("SECRETBAK")
    (root / "id_rsa").write_text("SECRETRSA")
    (root / ".secret.html").write_text("SECRETDOTHTML")
    (root / "sub" / "hidden.txt").write_text("SECRETHIDDEN")

    for path, needle in (
        ("/icon.svg", "<svg"),
        ("/logo.png", "PNG-ok"),
        ("/favicon.ico", "ICO-ok"),
        ("/app.js.map", '"file"'),
        ("/app.js?v=1", "console.log"),
    ):
        status, ctype, body = get(base + path)
        assert status == 200, path
        assert needle in body
        assert "text/html" not in ctype  # not the 404 page

    secrets = ("SECRETJSON", "SECRETTXT", "SECRETJPG", "SECRETWOFF", "SECRETDUMP",
               "SECRETZIP", "SECRETYML", "SECRETBAK", "SECRETRSA", "SECRETDOTHTML", "SECRETHIDDEN")
    for path in (
        "/backup.json", "/notes.txt", "/photo.jpg", "/font.woff2", "/dump.sql",
        "/archive.zip", "/config.yml", "/secret.bak", "/id_rsa", "/.secret.html",
        "/notes.txt?x=.js", "/backup.json?.html", "/sub/", "/sub/hidden.txt",
        "/no-such.html", "/missing", "/img/missing.png",
    ):
        status, _, body = get(base + path)
        assert status == 404, path
        assert "Directory listing" not in body
        assert not any(secret in body for secret in secrets)

    host, port = base.rsplit(":", 1)
    for path in ("/%2Esecret.html", "/%2Eenv", "/notes.txt%00.html"):
        conn = http.client.HTTPConnection(host.removeprefix("http://"), int(port), timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode()
            assert resp.status == 404, path
            assert "SECRETDOTHTML" not in body and "TOKEN" not in body
        finally:
            conn.close()


def test_is_servable_decodes_before_the_allowlist(serve):
    check = serve.DashboardHandler._is_servable
    assert check(None, "/") is True
    assert check(None, "/app.js?v=1") is True
    assert check(None, "/icon.SVG") is True
    assert check(None, "/app.js.map") is True
    assert check(None, "/logo.png") is True
    assert check(None, "/backup.json") is False
    assert check(None, "/notes.txt?.js") is False
    assert check(None, "/font.woff2") is False
    assert check(None, "/%2Esecret.html") is False
    assert check(None, "/%2Eenv") is False
    assert check(None, "/tests/t.js") is False
    assert check(None, "/sub/") is False
    assert check(None, "/notes.txt%00.html") is False


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
