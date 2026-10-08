"""#178: every metric an alert or recording rule reads must have a producer.

alerts.yml fired on ``redis_queue_length`` and ``pipeline_errors_total`` while
metrics_exporter.py sends ``redis.queue.length`` / ``errors.total`` to StatsD,
and the statsd-exporter catch-all renamed all of those to one ``statsd_``
series. The ingestor alerts read ``ingestor_*`` names kafka-delta-ingest never
emits. Alerts on missing series never fire, so this test builds the catalog of
names that really exist and checks every rule expression against it.
"""
from __future__ import annotations

import ast
import functools
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MON = ROOT / "monitoring"
HELM_FILES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring"
MAPPING = HELM_FILES / "statsd_mapping.yml"
JMX = HELM_FILES / "kafka_jmx_config.yml"
MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"
EXPORTER = MON / "metrics_exporter.py"
PROMTOOL_TEST = Path(__file__).with_name("alert_rules_promtool_test.yml")

RULE_FILES = [MON / "alerting_rules.yml", MON / "recording_rules.yml"]

# Third-party exporters: metric -> the scrape job that collects it.
EXTERNAL = {
    "up": None,
    "redis_memory_used_bytes": "redis",          # oliver006/redis_exporter
    "redis_memory_max_bytes": "redis",
    "pg_stat_database_numbackends": "postgres",  # postgres_exporter
    "pg_settings_max_connections": "postgres",
    "pg_stat_database_xact_commit": "postgres",
    "prometheus_tsdb_storage_blocks_bytes": None,  # Prometheus' own metrics
    "prometheus_tsdb_retention_limit_bytes": None,
}

PROMQL_KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right",
                   "bool", "and", "or", "unless", "offset", "inf", "nan"}


def _python_metrics() -> dict[str, str]:
    """Names passed to prometheus_client Counter/Gauge/Histogram/Summary."""
    found: dict[str, str] = {}
    for path in [*(ROOT / "src").rglob("*.py"), *MON.rglob("*.py")]:
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            arg = node.args[0]
            if (re.search(r"(Counter|Gauge|Histogram|Summary)$", name)
                    and isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", arg.value)):
                found[arg.value] = str(path.relative_to(ROOT))
    return found


def _mappings() -> list[dict]:
    return yaml.safe_load(MAPPING.read_text())["mappings"]


def _statsd_metrics() -> set[str]:
    return {m["name"] for m in _mappings() if "$" not in m["name"]}


def _jmx_metrics() -> set[str]:
    rules = yaml.safe_load(JMX.read_text())["rules"]
    return {r["name"] for r in rules if "name" in r and "$" not in r["name"]}


def _rules(path: Path) -> list[dict]:
    return [r for g in yaml.safe_load(path.read_text())["groups"] for r in g["rules"]]


def _recorded() -> set[str]:
    return {r["record"] for p in RULE_FILES for r in _rules(p) if "record" in r}


def _grafana_exprs() -> list[tuple[str, str]]:
    doc = yaml.safe_load((MON / "alerting" / "rules.yml").read_text())
    return [(r["uid"], q["model"]["expr"]) for g in doc["groups"] for r in g["rules"]
            for q in r["data"] if q.get("model", {}).get("expr")]


def _rule_exprs() -> list[tuple[str, str]]:
    out = [(f"{p.name}:{r.get('alert') or r.get('record')}", r["expr"])
           for p in RULE_FILES for r in _rules(p)]
    return out + [(f"alerting/rules.yml:{uid}", e) for uid, e in _grafana_exprs()]


def metric_names(expr: str) -> set[str]:
    """Series names in a PromQL expression (good enough for our rule files)."""
    e = re.sub(r'"[^"]*"|\'[^\']*\'', "", expr)
    e = re.sub(r"\{[^}]*\}", "", e)
    e = re.sub(r"\[[^\]]*\]", "", e)
    e = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", e)
    names = set()
    for m in re.finditer(r"(?<![0-9.a-zA-Z_:])[a-zA-Z_:][a-zA-Z0-9_:]*", e):
        if e[m.end():].lstrip().startswith("(") or m.group(0) in PROMQL_KEYWORDS:
            continue
        names.add(m.group(0))
    return names


@functools.lru_cache(maxsize=None)
def _catalog() -> frozenset[str]:
    base = set(_python_metrics()) | _statsd_metrics() | _jmx_metrics() | _recorded() | set(EXTERNAL)
    return frozenset(base | {f"{n}_{s}" for n in base for s in ("bucket", "sum", "count")})


def test_metric_name_parser():
    assert metric_names('sum by (stage) (rate(pipeline_errors_total{a="x"}[5m])) > 1e-9') == {
        "pipeline_errors_total"}
    assert metric_names("histogram_quantile(0.95, sum by (le) (rate(x_bucket[5m] offset 1h)))") == {
        "x_bucket"}
    assert metric_names("a > 0 and on() (sum(rate(b[1m])) or vector(0)) == 0") == {"a", "b"}


@pytest.mark.parametrize("rule,expr", _rule_exprs(), ids=lambda v: v if ":" in str(v) else "")
def test_every_rule_metric_has_a_producer(rule, expr):
    missing = sorted(metric_names(expr) - _catalog())
    assert not missing, f"{rule} reads metrics nothing produces: {missing}"


def test_the_old_phantom_names_are_gone():
    exprs = " ".join(e for _, e in _rule_exprs())
    for phantom in ("ingestor_records_failed_total", "ingestor_batch_write_latency_ms",
                    "ingestor_delta_commits_total", "kafka_messages_consumed_total",
                    "kafka_messages_processed_total", "kafka_messages_failed_total",
                    "delta_batch_write_seconds", "delta_batch_size", "kafka_consumer_lag "):
        assert phantom not in exprs + " ", phantom


def test_external_exporter_jobs_are_scraped():
    jobs = {j["job_name"] for j in yaml.safe_load((MON / "prometheus.yml").read_text())["scrape_configs"]}
    helm = (ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates" / "prometheus-statefulset.yaml").read_text()
    for metric, job in EXTERNAL.items():
        if job:
            assert job in jobs, f"{metric} needs scrape job {job!r} in monitoring/prometheus.yml"
            assert f"job_name: '{job}'" in helm, f"{metric} needs scrape job {job!r} in Helm"


def test_up_selectors_name_real_jobs():
    jobs = {j["job_name"] for j in yaml.safe_load((MON / "prometheus.yml").read_text())["scrape_configs"]}
    for rule, expr in _rule_exprs():
        for job in re.findall(r'up\{job="([^"]+)"\}', expr):
            assert job in jobs, f"{rule}: up{{job={job!r}}} matches no scrape job"


def test_prometheus_loads_every_rule_file_and_alerts_yml_is_gone():
    loaded = yaml.safe_load((MON / "prometheus.yml").read_text())["rule_files"]
    for path in loaded:
        assert (MON / Path(path).name).is_file(), path
    # alerts.yml was never in rule_files, so its alerts could not fire (#178).
    assert not (MON / "alerts.yml").exists()


def test_helm_rule_files_match_monitoring():
    for name in ("alerting_rules.yml", "recording_rules.yml"):
        assert (HELM_FILES / name).read_text() == (MON / name).read_text(), (
            f"k8s/helm/.../files/monitoring/{name} drifted from monitoring/{name}")


def test_exporter_statsd_names_are_mapped():
    src = EXPORTER.read_text()
    by_match = {m["match"]: m["name"] for m in _mappings()}
    for statsd_name, prom_name in (("redis.queue.length", "redis_queue_length"),
                                   ("errors.total", "pipeline_errors_total"),
                                   ("circuit_breaker.open_count", "circuit_breaker_open_count")):
        assert f'"{statsd_name}"' in src, f"metrics_exporter.py no longer sends {statsd_name}"
        assert by_match.get(statsd_name) == prom_name


def test_regex_mappings_capture_what_they_substitute():
    for m in _mappings():
        refs = [int(n) for n in re.findall(r"\$\{?(\d+)", m["name"])]
        if not refs:
            continue
        pat = m["match"]
        groups = re.compile(pat).groups if m.get("match_type") == "regex" else pat.count("*")
        assert max(refs) <= groups, f"{pat!r} -> {m['name']!r} references a missing group"


def test_ingestor_emits_the_batch_write_timer():
    src = MAIN_RS.read_text()
    assert re.search(r'metrics\.time\("batch\.write",', src)
    timer = next(m for m in _mappings() if m["match"] == "kafka_delta_ingest.batch.write")
    assert timer["name"] == "kafka_delta_ingest_batch_write_seconds"
    assert timer["observer_type"] == "histogram"


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool not installed")
def test_promtool_unit_tests_pass():
    for path in RULE_FILES:
        subprocess.run(["promtool", "check", "rules", str(path)], check=True, capture_output=True)
    res = subprocess.run(["promtool", "test", "rules", PROMTOOL_TEST.name],
                         cwd=PROMTOOL_TEST.parent, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
