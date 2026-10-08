"""#625: .dockerignore keeps Dockerfile/compose and the monitoring exporter in the build context, documents every exclusion."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _patterns():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def test_dockerfile_compose_and_monitoring_not_excluded():
    pats = _patterns()
    for keep in ("Dockerfile", "docker-compose.yml", "monitoring/", "monitoring"):
        assert keep not in pats, f"{keep} must stay in the build context"
    # #449: monitoring configs are excluded, but the exporter code the `metrics` target runs ships.
    if "monitoring/*" in pats:
        assert {"!monitoring/metrics_exporter.py", "!monitoring/metric_helpers.py"} <= set(pats)
    assert (ROOT / "monitoring" / "prometheus.yml").exists()


def test_heavy_or_runtime_paths_still_excluded():
    pats = set(_patterns())
    assert {".git/", "data/", "logs/", "kafka-delta-ingest/target/"} <= pats


def test_every_exclusion_block_is_commented():
    block_has_comment = False
    for raw in (ROOT / ".dockerignore").read_text().splitlines():
        line = raw.strip()
        if not line:
            block_has_comment = False
            continue
        if line.startswith("#"):
            block_has_comment = True
            continue
        assert block_has_comment, f"uncommented .dockerignore entry: {line}"


def test_compose_bind_mounts_monitoring_configs():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml" in compose
    assert "./monitoring/alerting:/etc/grafana/provisioning/alerting" in compose
