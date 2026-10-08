"""monitoring/prometheus.yml is the docker compose config: it may only scrape
what compose runs, and compose must mount every rule file it loads (#371)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PROM = ROOT / "monitoring" / "prometheus.yml"
COMPOSE = ROOT / "docker-compose.yml"
HELM = ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates" / "prometheus-statefulset.yaml"


def load_prom_config():
    return yaml.safe_load(PROM.read_text())


def _compose():
    return yaml.safe_load(COMPOSE.read_text())


def _targets(config):
    for job in config["scrape_configs"]:
        for sc in job.get("static_configs", []):
            for target in sc["targets"]:
                yield job["job_name"], target


def test_every_scrape_target_is_a_compose_service():
    services = set(_compose()["services"])
    for job, target in _targets(load_prom_config()):
        host = target.rsplit(":", 1)[0]
        if host == "localhost":
            assert job == "prometheus", f"{job}: localhost is the Prometheus container itself"
            continue
        assert host in services, f"job {job!r} scrapes {target}, but compose has no {host!r} service"


def test_no_alertmanagers_compose_does_not_run():
    config = load_prom_config()
    services = set(_compose()["services"])
    for am in (config.get("alerting") or {}).get("alertmanagers", []):
        for sc in am.get("static_configs", []):
            for target in sc["targets"]:
                assert target.rsplit(":", 1)[0] in services, target


def test_redis_and_postgres_are_scraped_through_exporters():
    targets = dict(_targets(load_prom_config()))
    assert targets["redis"] == "redis-exporter:9121"  # redis:6379 is not an HTTP metrics endpoint
    assert targets["postgres"] == "postgres-exporter:9187"
    services = _compose()["services"]
    assert "redis://redis:6379" in " ".join(services["redis-exporter"]["environment"])
    assert "@postgres:5432/" in " ".join(services["postgres-exporter"]["environment"])


def test_scrapy_app_targets_the_scraper_metrics_port():
    from src import settings

    targets = [t for j, t in _targets(load_prom_config()) if j == "scrapy_app"]
    assert targets == [f"scraper:{settings.PROMETHEUS_PORT}"]
    assert "src.scrapy_prometheus.PrometheusExtension" in settings.EXTENSIONS


def test_rule_files_are_mounted_into_the_container():
    volumes = _compose()["services"]["prometheus"]["volumes"]
    mounts = {v.split(":")[1]: v.split(":")[0] for v in volumes if v.startswith("./")}
    for rule_file in load_prom_config()["rule_files"]:
        assert rule_file in mounts, f"{rule_file} is in rule_files but not mounted by docker-compose.yml"
        assert (ROOT / mounts[rule_file]).is_file()


def test_external_labels_have_no_unexpanded_env_vars():
    labels = load_prom_config()["global"].get("external_labels", {})
    for key, value in labels.items():
        assert "${" not in str(value), f"Prometheus does not expand env vars: {key}={value}"


def test_core_jobs_present():
    jobs = {job["job_name"] for job in load_prom_config()["scrape_configs"]}
    assert {"prometheus", "scrapy_app", "redis", "postgres"} <= jobs
    # kafka-delta-ingest only speaks StatsD; scraping it directly is always down (#178).
    assert "kafka_ingestor" not in jobs


def test_helm_keeps_the_full_stack_jobs_and_alertmanagers():
    helm = HELM.read_text()
    for job in ("scrapy_app", "scraping_pipeline", "redis", "postgres", "kafka_jmx", "statsd"):
        assert f"job_name: '{job}'" in helm
    assert re.search(r"alertmanagers:", helm)


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool not installed")
def test_promtool_accepts_config_and_mounted_rules(tmp_path):
    text = PROM.read_text().replace("/etc/prometheus/", f"{tmp_path}/")
    (tmp_path / "prometheus.yml").write_text(text)
    for rule_file in load_prom_config()["rule_files"]:
        name = Path(rule_file).name
        shutil.copy(ROOT / "monitoring" / name, tmp_path / name)
    result = subprocess.run(
        ["promtool", "check", "config", str(tmp_path / "prometheus.yml")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_scripts_readme_port_table_matches_compose():
    text = (ROOT / "scripts" / "README.md").read_text()
    section = text.split("### Docker Compose:", 1)[1].split("### Kubernetes:", 1)[0]
    published = set()
    for svc in _compose()["services"].values():
        for port in svc.get("ports", []):
            published.add(str(port).split(":")[-2] if str(port).count(":") else str(port))
    rows = re.findall(r"^\| [^|]+ \| (\d+) \|", section, re.M)
    assert rows, "port table missing"
    for port in rows:
        assert port in published, f"scripts/README.md lists port {port}, which docker-compose.yml does not publish"
