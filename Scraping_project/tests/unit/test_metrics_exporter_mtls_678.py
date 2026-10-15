"""Optional mTLS between the metrics exporter and Prometheus (#678).

Default dev stays plain HTTP: the Helm flag is off, and the compose
Prometheus config does not grow a TLS scrape.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from monitoring import metrics_exporter as exporter_module

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
PROM = ROOT / "monitoring" / "prometheus.yml"


def _values():
    return yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))


def test_mtls_flag_defaults_off():
    mtls = _values()["metricsExporter"]["mtls"]
    assert mtls["enabled"] is False
    assert mtls["issueCerts"] is True
    assert mtls["issuerRef"]["name"] == ""
    assert mtls["issuerRef"]["kind"] == "ClusterIssuer"
    assert mtls["serverSecretName"] == ""
    assert mtls["clientSecretName"] == ""
    assert mtls["serverName"] == ""
    prod = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
    assert "metricsExporter" not in prod


def test_compose_prometheus_stays_plain_http():
    text = PROM.read_text(encoding="utf-8")
    assert "metricsExporter.mtls.enabled" in text
    cfg = yaml.safe_load(text)
    for job in cfg["scrape_configs"]:
        assert "tls_config" not in job
        assert job.get("scheme", "http") == "http"


def test_helm_templates_gate_mtls_on_the_flag():
    prom = (CHART / "templates" / "prometheus-statefulset.yaml").read_text(encoding="utf-8")
    job = prom.split("job_name: 'scraping_pipeline'", 1)[1].split("job_name:", 1)[0]
    assert "metricsExporter.mtls.enabled" in job
    assert job.index("metricsExporter.mtls.enabled") < job.index("scheme: https")
    assert "/etc/prometheus-mtls/ca.crt" in job
    assert "/etc/prometheus-mtls/tls.crt" in job
    assert "/etc/prometheus-mtls/tls.key" in job
    assert "metricsExporterMtlsServerName" in job
    assert "metricsExporterMtlsClientSecret" in prom

    dep = (CHART / "templates" / "metrics-exporter-deployment.yaml").read_text(encoding="utf-8")
    assert ".Values.redis.auth.existingSecret" in dep
    assert dep.index("metricsExporter.mtls.enabled") < dep.index("METRICS_EXPORTER_TLS_CERT")
    assert "METRICS_EXPORTER_TLS_KEY" in dep and "METRICS_EXPORTER_TLS_CA" in dep
    assert "/healthz{{ else }}/metrics" in dep

    certs = (CHART / "templates" / "metrics-exporter-mtls.yaml").read_text(encoding="utf-8")
    assert certs.startswith(
        "{{- if and .Values.metricsExporter.enabled .Values.metricsExporter.mtls.enabled "
        ".Values.metricsExporter.mtls.issueCerts }}"
    )
    assert certs.count("kind: Certificate") == 2
    assert "server auth" in certs and "client auth" in certs

    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "issuerRef.name" in helpers
    assert "issueCerts=false requires" in helpers


def test_readme_documents_cert_manager_and_scrape_config():
    readme = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
    assert "metricsExporter.mtls.enabled" in readme
    assert "cert-manager.io/v1" in readme
    assert "tls_config:" in readme
    assert "Let's Encrypt" in readme
    guide = (ROOT / "docs" / "guides" / "MONITORING.md").read_text(encoding="utf-8")
    assert "metricsExporter.mtls.enabled" in guide


def test_tls_env_is_all_or_nothing():
    assert exporter_module.tls_materials_from_env({}) is None
    with pytest.raises(ValueError):
        exporter_module.tls_materials_from_env({"METRICS_EXPORTER_TLS_CERT": "/cert"})
    with pytest.raises(ValueError):
        exporter_module.tls_materials_from_env({"METRICS_EXPORTER_TLS_CA": "/ca"})
    got = exporter_module.tls_materials_from_env(
        {
            "METRICS_EXPORTER_TLS_CERT": "/cert",
            "METRICS_EXPORTER_TLS_KEY": "/key",
            "METRICS_EXPORTER_TLS_CA": "/ca",
        }
    )
    assert got == {"cert": "/cert", "key": "/key", "ca": "/ca"}


def test_main_passes_tls_only_when_env_is_set(monkeypatch):
    captured: dict = {}

    class Stub:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def start(self, health_port=0, tls=None):
            captured["port"] = health_port
            captured["tls"] = tls

    monkeypatch.setattr(exporter_module, "MetricsExporter", Stub)
    for name in ("METRICS_EXPORTER_TLS_CERT", "METRICS_EXPORTER_TLS_KEY", "METRICS_EXPORTER_TLS_CA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["metrics_exporter.py", "--port", "9100", "--interval", "5"])
    exporter_module.main()
    assert captured["port"] == 9100 and captured["tls"] is None

    monkeypatch.setenv("METRICS_EXPORTER_TLS_CA", "/ca-only")
    with pytest.raises(SystemExit):
        exporter_module.main()

    monkeypatch.setenv("METRICS_EXPORTER_TLS_CERT", "/cert")
    monkeypatch.setenv("METRICS_EXPORTER_TLS_KEY", "/key")
    exporter_module.main()
    assert captured["tls"] == {"cert": "/cert", "key": "/key", "ca": "/ca-only"}


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _ext(path: Path, body: str) -> None:
    if not body.endswith("\n"):
        body += "\n"
    path.write_text("[v3]\n" + body)


def _sign(cwd: Path, csr: str, out: str, ext: str) -> None:
    _openssl(
        "x509", "-req", "-in", csr, "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-out", out, "-days", "1", "-sha256",
        "-extfile", ext, "-extensions", "v3",
        cwd=cwd,
    )


def _make_ca(cwd: Path, cn: str) -> None:
    # OpenSSL 3 will not treat this as a trust anchor for a client cert unless
    # basicConstraints says CA:TRUE. `req -x509` takes -addext, not -extfile.
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-keyout", "ca.key", "-out", "ca.crt",
        "-days", "1", "-nodes", "-subj", f"/CN={cn}",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        cwd=cwd,
    )


def _write_certs(tmp_path: Path) -> dict[str, Path]:
    _make_ca(tmp_path, "test-ca")
    _ext(tmp_path / "server.ext", "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
    _openssl("req", "-new", "-nodes", "-keyout", "server.key", "-out", "server.csr",
             "-subj", "/CN=localhost", cwd=tmp_path)
    _sign(tmp_path, "server.csr", "server.crt", "server.ext")
    _ext(tmp_path / "client.ext", "extendedKeyUsage=clientAuth\n")
    _openssl("req", "-new", "-nodes", "-keyout", "client.key", "-out", "client.csr",
             "-subj", "/CN=prometheus", cwd=tmp_path)
    _sign(tmp_path, "client.csr", "client.crt", "client.ext")
    other = tmp_path / "other"
    other.mkdir()
    _make_ca(other, "other-ca")
    _openssl("req", "-new", "-nodes", "-keyout", "client.key", "-out", "client.csr",
             "-subj", "/CN=stranger", cwd=other)
    _ext(other / "client.ext", "extendedKeyUsage=clientAuth\n")
    _sign(other, "client.csr", "client.crt", "client.ext")
    return {
        "ca": tmp_path / "ca.crt",
        "cert": tmp_path / "server.crt",
        "key": tmp_path / "server.key",
        "client_cert": tmp_path / "client.crt",
        "client_key": tmp_path / "client.key",
        "other_cert": other / "client.crt",
        "other_key": other / "client.key",
    }


def _open(url: str, ca: Path, cert: Path | None = None, key: Path | None = None):
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=str(ca))
    if cert is not None and key is not None:
        ctx.load_cert_chain(str(cert), str(key))
    return urllib.request.urlopen(url, context=ctx, timeout=5)


class _StubExporter:
    def health_text(self) -> str:
        return "metrics_exporter_up 1\n"

    def is_healthy(self) -> bool:
        return True


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_metrics_requires_client_cert_and_healthz_does_not(tmp_path):
    paths = _write_certs(tmp_path)
    server = exporter_module.MetricsExporter.start_health_server(
        _StubExporter(),
        0,
        host="127.0.0.1",
        tls={"cert": str(paths["cert"]), "key": str(paths["key"]), "ca": str(paths["ca"])},
    )
    port = server.server_address[1]
    try:
        health = _open(f"https://127.0.0.1:{port}/healthz", paths["ca"])
        assert health.status == 200
        assert health.read() == b"ok\n"
        with pytest.raises(urllib.error.HTTPError) as err:
            _open(f"https://127.0.0.1:{port}/metrics", paths["ca"])
        assert err.value.code == 401
        body = _open(
            f"https://127.0.0.1:{port}/metrics",
            paths["ca"],
            paths["client_cert"],
            paths["client_key"],
        ).read()
        assert b"metrics_exporter_up 1" in body
        with pytest.raises((urllib.error.URLError, ssl.SSLError)) as bad:
            _open(
                f"https://127.0.0.1:{port}/metrics",
                paths["ca"],
                paths["other_cert"],
                paths["other_key"],
            )
        assert not isinstance(bad.value, urllib.error.HTTPError)
    finally:
        server.shutdown()
        server.server_close()
