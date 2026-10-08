"""Postgres metrics sink failures are counted, logged, and never raised (#586)."""
import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from src.utils import metrics_sink
from src.utils.metrics_sink import record_error, record_performance

prom = pytest.importorskip("prometheus_client")
REGISTRY = prom.REGISTRY


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class DownPostgres:
    def log_performance_metric(self, **kw):
        raise ConnectionError("connection refused")

    def log_error(self, **kw):
        raise ConnectionError("connection refused")


def test_failed_performance_write_is_counted_logged_and_not_raised(caplog):
    before_fail = sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="failure")
    before_urls = sample("scrapy_stage_urls_processed_total", stage="stage2")
    with caplog.at_level(logging.WARNING, logger="src.utils.metrics_sink"):
        assert record_performance(DownPostgres(), "stage2", 40, 2.5, worker_count=8) is False
    assert sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="failure") == before_fail + 1
    # Dual-export: Prometheus got the data even though Postgres didn't.
    assert sample("scrapy_stage_urls_processed_total", stage="stage2") == before_urls + 40
    line = next(r.getMessage() for r in caplog.records if "metrics_sink_fallback" in r.getMessage())
    assert "kind=performance" in line and "ConnectionError" in line
    payload = json.loads(line.split("payload=", 1)[1])
    assert payload["urls_processed"] == 40 and payload["worker_count"] == 8


def test_failed_error_write_is_counted_logged_and_not_raised(caplog):
    before = sample("scrapy_pg_metrics_writes_total", kind="error", outcome="failure")
    before_err = sample("scrapy_stage_errors_total", stage="stage3", error_type="TimeoutError")
    with caplog.at_level(logging.WARNING, logger="src.utils.metrics_sink"):
        ok = record_error(DownPostgres(), "stage3", "https://x.example/a", "TimeoutError", "x" * 5000)
    assert ok is False
    assert sample("scrapy_pg_metrics_writes_total", kind="error", outcome="failure") == before + 1
    assert sample("scrapy_stage_errors_total", stage="stage3", error_type="TimeoutError") == before_err + 1
    line = next(r.getMessage() for r in caplog.records if "metrics_sink_fallback" in r.getMessage())
    payload = json.loads(line.split("payload=", 1)[1])
    assert payload["url"] == "https://x.example/a"
    assert len(payload["error_message"]) == metrics_sink.MAX_FALLBACK_MESSAGE_CHARS


def test_successful_writes_are_counted_and_forwarded():
    pg = MagicMock()
    before = sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="success")
    assert record_performance(pg, "stage3", 5, 1.0, worker_count=2) is True
    pg.log_performance_metric.assert_called_once_with(
        stage="stage3", urls_processed=5, processing_time_seconds=1.0,
        worker_count=2, memory_usage_mb=None)
    assert sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="success") == before + 1
    assert record_error(pg, "stage2", "u", "HTTPError", "boom", http_status_code=503) is True
    pg.log_error.assert_called_once()
    assert pg.log_error.call_args.kwargs["http_status_code"] == 503


def test_disabled_postgres_still_exports_to_prometheus():
    before_dis = sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="disabled")
    before_secs = sample("scrapy_stage_batch_seconds_total", stage="stage9")
    assert record_performance(None, "stage9", 3, 0.75) is False
    assert sample("scrapy_pg_metrics_writes_total", kind="performance", outcome="disabled") == before_dis + 1
    assert sample("scrapy_stage_batch_seconds_total", stage="stage9") == before_secs + 0.75


def test_long_error_type_label_is_bounded():
    record_error(None, "stage2", "u", "E" * 500)
    assert sample("scrapy_stage_errors_total", stage="stage2",
                  error_type="E" * metrics_sink.MAX_LABEL_CHARS) >= 1


def test_stage_workers_route_through_the_sink_not_bare_try_except():
    """Source guard: no stage worker swallows a Postgres write at DEBUG again."""
    root = Path(__file__).resolve().parents[2] / "src"
    for rel in ("stage2/stage2_worker.py", "stage3/stage3_worker.py"):
        text = (root / rel).read_text()
        assert "postgres.log_performance_metric(" not in text, rel
        assert "postgres.log_error(" not in text, rel
        assert "record_performance(" in text or "record_error(" in text, rel


def test_stage2_error_helper_survives_a_dead_postgres(caplog):
    from src.stage2.stage2_worker import Stage2Worker
    worker = Stage2Worker.__new__(Stage2Worker)
    worker.postgres = DownPostgres()
    before = sample("scrapy_pg_metrics_writes_total", kind="error", outcome="failure")
    with caplog.at_level(logging.WARNING, logger="src.utils.metrics_sink"):
        worker._log_error_to_postgres("https://x.example/b", "TimeoutError", "slow")
    assert sample("scrapy_pg_metrics_writes_total", kind="error", outcome="failure") == before + 1
    assert any("metrics_sink_fallback" in r.getMessage() for r in caplog.records)


def test_alert_rule_exists_and_targets_the_failure_counter():
    rules = yaml.safe_load((Path(__file__).resolve().parents[2]
                            / "monitoring" / "alerting_rules.yml").read_text())
    alerts = {r["alert"]: r for g in rules["groups"] for r in g["rules"] if "alert" in r}
    rule = alerts["PostgresMetricsSinkFailing"]
    assert 'scrapy_pg_metrics_writes_total{outcome="failure"}' in rule["expr"]
    assert rule["labels"]["severity"] == "warning"
