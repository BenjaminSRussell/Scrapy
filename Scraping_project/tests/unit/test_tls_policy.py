"""#584: TLS certificate verification on by default; insecure override gated."""

import os
import re
import shutil
import ssl
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.core import tls_policy

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# Patterns that disable certificate verification in the clients this repo uses.
BYPASS_PATTERNS = [
    r"verify\s*=\s*False",
    r"\bssl\s*=\s*False",
    r"CERT_NONE",
    r"check_hostname\s*=\s*False",
    r"_create_unverified_context",
    r"ignore_https_errors['\"]?\s*[:=]\s*True",
    r"ScrapyClientContextFactory",
]
ALLOWED = {SRC / "core" / "tls_policy.py"}


def test_no_tls_bypass_in_source():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in BYPASS_PATTERNS:
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)}")
    assert not offenders, "TLS verification bypass (see src/core/tls_policy.py):\n" + "\n".join(offenders)


def test_policy_defaults_to_verifying_factory():
    assert tls_policy.downloader_context_factory({}) == tls_policy.VERIFYING_FACTORY
    assert tls_policy.downloader_context_factory({"SCRAPY_TLS_INSECURE": "0"}) == tls_policy.VERIFYING_FACTORY


def test_insecure_override_is_explicit_loud_and_metered(caplog):
    with caplog.at_level("ERROR"):
        factory = tls_policy.downloader_context_factory({"SCRAPY_TLS_INSECURE": "1"})
    assert factory == tls_policy.INSECURE_FACTORY
    assert "DISABLED" in caplog.text
    if tls_policy.TLS_VERIFICATION_DISABLED is not None:
        from prometheus_client import REGISTRY

        assert REGISTRY.get_sample_value("scrapy_tls_verification_disabled") == 1.0
        tls_policy.downloader_context_factory({})
        assert REGISTRY.get_sample_value("scrapy_tls_verification_disabled") == 0.0


def test_project_settings_verify_tls():
    from src import settings

    assert settings.DOWNLOADER_CLIENTCONTEXTFACTORY == tls_policy.VERIFYING_FACTORY


CRAWL = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})
    import scrapy
    from scrapy.crawler import CrawlerProcess
    from src.core.tls_policy import downloader_context_factory

    result = []

    class Probe(scrapy.Spider):
        name = "tls_probe"
        def start_requests(self):
            yield scrapy.Request({url!r}, callback=self.ok, errback=self.err, dont_filter=True)
        def ok(self, response):
            result.append("fetched")
        def err(self, failure):
            reasons = getattr(failure.value, "reasons", None) or [failure]
            detail = " | ".join(repr(r.value) for r in reasons)
            result.append("rejected:" + failure.type.__name__ + ":" + detail.replace(chr(10), " ")[:500])

    process = CrawlerProcess({{
        "DOWNLOADER_CLIENTCONTEXTFACTORY": downloader_context_factory(),
        "RETRY_ENABLED": False, "LOG_LEVEL": "ERROR", "TELNETCONSOLE_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
    }})
    process.crawl(Probe)
    process.start()
    print("RESULT", result[0] if result else "none")
    """
)


@pytest.fixture
def self_signed_https(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>spoofed</html>")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{server.server_address[1]}/"
    server.shutdown()


def _crawl(url, tmp_path, insecure):
    script = tmp_path / "crawl.py"
    script.write_text(CRAWL.format(root=str(ROOT), url=url))
    env = {k: v for k, v in os.environ.items() if k != "SCRAPY_TLS_INSECURE"}
    if insecure:
        env["SCRAPY_TLS_INSECURE"] = "1"
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path)
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, out.stderr[-2000:]
    return lines[-1].split(" ", 1)[1]


def test_downloader_rejects_untrusted_certificate(self_signed_https, tmp_path):
    outcome = _crawl(self_signed_https, tmp_path, insecure=False)
    assert outcome.startswith("rejected:"), outcome
    assert "certificate verify failed" in outcome or "self-signed" in outcome.lower(), outcome


def test_insecure_override_accepts_it(self_signed_https, tmp_path):
    assert _crawl(self_signed_https, tmp_path, insecure=True) == "fetched"
