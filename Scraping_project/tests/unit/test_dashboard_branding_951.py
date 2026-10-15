"""Dashboard branding (#951): CC_SITE_NAME / config.json inject data-site-name.

The shipped index.html stays the UConn wording when neither is set.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_brand_951", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _serve(serve, root: Path):
    handler = type("H", (serve.DashboardHandler,), {"root": root, "log_message": lambda *a: None})
    httpd = serve.make_server("127.0.0.1", 0, handler=handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_sanitize_and_resolve_prefer_env_over_config(serve, tmp_path):
    assert serve.sanitize_site_name("  Acme \n Lab ") == "Acme Lab"
    assert serve.sanitize_site_name("<script>") == ""
    assert serve.sanitize_site_name("x" * 81) == ""
    assert serve.sanitize_site_name("A & B") == "A & B"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"siteName": "FromFile"}), encoding="utf-8")
    assert serve.resolve_site_name({}, cfg) == "FromFile"
    assert serve.resolve_site_name({"CC_SITE_NAME": "FromEnv"}, cfg) == "FromEnv"
    assert serve.resolve_site_name({"CC_SITE_NAME": "<b>"}, cfg) == "FromFile"
    assert serve.resolve_site_name({}, tmp_path / "missing.json") == ""
    cfg.write_text("{", encoding="utf-8")
    assert serve.site_name_from_config(cfg) == ""


def test_inject_escapes_and_leaves_default_and_authored_attribute(serve):
    page = (DASH / "index.html").read_text(encoding="utf-8")
    assert "data-site-name" not in page
    assert "UConn Web Scraping Pipeline - Real-time Operations Dashboard" in page
    assert serve.inject_data_site_name(page, "") is None
    assert serve.inject_data_site_name(page, "<script>alert(1)</script>") is None
    branded = serve.inject_data_site_name(page, 'A & "B"')
    assert branded is not None
    assert '<html lang="en" data-site-name="A &amp; &quot;B&quot;">' in branded
    assert "UConn Web Scraping Pipeline" in branded
    assert serve.inject_data_site_name('<html data-site-name="Kept">', "Acme") is None
    assert serve.inject_data_site_name("<!doctype html><title>CC</title>", "Acme") is None


def test_server_injects_only_when_configured(serve, tmp_path, monkeypatch):
    monkeypatch.delenv("CC_SITE_NAME", raising=False)
    root = tmp_path / "dash"
    root.mkdir()
    page = "<!DOCTYPE html>\n<html lang=\"en\"><title>Pipeline Control Center - UConn Scraping</title></html>\n"
    (root / "index.html").write_text(page, encoding="utf-8")

    httpd = _serve(serve, root)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, body = _get(base + "/")
        assert status == 200 and body == page and "data-site-name" not in body

        (root / "config.json").write_text(json.dumps({"siteName": "FromFile"}), encoding="utf-8")
        status, body = _get(base + "/index.html")
        assert status == 200
        assert 'data-site-name="FromFile"' in body
        assert "UConn Scraping" in body

        monkeypatch.setenv("CC_SITE_NAME", "FromEnv")
        _, body = _get(base + "/")
        assert 'data-site-name="FromEnv"' in body

        # A rejected env value does not erase a valid config.json name.
        monkeypatch.setenv("CC_SITE_NAME", "<script>")
        _, body = _get(base + "/")
        assert 'data-site-name="FromFile"' in body

        (root / "config.json").unlink()
        _, body = _get(base + "/")
        assert body == page and "data-site-name" not in body
    finally:
        httpd.shutdown()
        httpd.server_close()
