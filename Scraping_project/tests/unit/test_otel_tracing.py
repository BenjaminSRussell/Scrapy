"""Unit tests for OpenTelemetry tracing helpers (no collector required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src import otel_tracing as ot


@pytest.fixture(autouse=True)
def _reset_otel_state(monkeypatch):
    """Keep module globals isolated across tests; clear OTEL env by default."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("CRAWL_JOB_ID", raising=False)
    ot.reset_tracing_state_for_tests()
    yield
    ot.reset_tracing_state_for_tests()


class TestNoOpWithoutEndpoint:
    def test_is_otel_enabled_false_when_unset(self):
        assert ot.is_otel_enabled() is False

    def test_init_tracing_noop_without_endpoint(self):
        assert ot.init_tracing(service_name="test-svc") is False
        assert ot._enabled is False
        assert ot.get_tracer() is None

    def test_start_span_yields_none_without_endpoint(self):
        with ot.start_span("demo", stage="stage1", spider="scout") as span:
            assert span is None

    def test_init_tracing_noop_when_sdk_missing(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        monkeypatch.setattr(ot, "_OTEL_SDK_AVAILABLE", False)
        ot.reset_tracing_state_for_tests()
        assert ot.init_tracing() is False
        assert ot._enabled is False


class TestEnsureCrawlJobIdAndAttributes:
    def test_ensure_crawl_job_id_honors_env(self, monkeypatch):
        monkeypatch.setenv("CRAWL_JOB_ID", "job-from-env")
        assert ot.ensure_crawl_job_id() == "job-from-env"
        assert ot.get_crawl_job_id() == "job-from-env"

    def test_ensure_crawl_job_id_generates_uuid_when_unset(self):
        job_id = ot.ensure_crawl_job_id()
        assert isinstance(job_id, str)
        assert len(job_id) >= 8
        assert ot.get_crawl_job_id() == job_id
        # Second call returns the same id
        assert ot.ensure_crawl_job_id() == job_id

    def test_base_attributes_wire_scrapy_keys(self):
        ot.set_spider_name("scout")
        ot.set_crawl_job_id("crawl-abc")
        attrs = ot._base_attributes(stage="stage2")
        assert attrs["scrapy.stage"] == "stage2"
        assert attrs["scrapy.spider"] == "scout"
        assert attrs["scrapy.crawl_job_id"] == "crawl-abc"

    def test_base_attributes_explicit_overrides_context(self):
        ot.set_spider_name("scout")
        ot.set_crawl_job_id("ctx-id")
        attrs = ot._base_attributes(
            stage="stage3",
            spider="other",
            crawl_job_id="explicit-id",
            extra={"custom": 1},
        )
        assert attrs == {
            "scrapy.stage": "stage3",
            "scrapy.spider": "other",
            "scrapy.crawl_job_id": "explicit-id",
            "custom": 1,
        }

    def test_start_span_passes_attributes_when_enabled(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_span
        mock_cm.__exit__.return_value = False
        mock_tracer.start_as_current_span.return_value = mock_cm

        ot._initialized = True
        ot._enabled = True
        ot._tracer = mock_tracer

        with ot.start_span(
            "stage2.run",
            stage="stage2",
            spider="worker",
            crawl_job_id="job-99",
        ) as span:
            assert span is mock_span

        mock_tracer.start_as_current_span.assert_called_once()
        call_kwargs = mock_tracer.start_as_current_span.call_args
        assert call_kwargs[0][0] == "stage2.run"
        attrs = call_kwargs[1]["attributes"]
        assert attrs["scrapy.stage"] == "stage2"
        assert attrs["scrapy.spider"] == "worker"
        assert attrs["scrapy.crawl_job_id"] == "job-99"


class TestExtensionSpiderOpenedNoBreak:
    def test_spider_opened_sets_crawl_job_id_when_otel_unset(self):
        ext = ot.OtelTracingExtension()
        spider = SimpleNamespace(name="scout")

        # Must not raise even though tracing is disabled
        ext.spider_opened(spider)

        assert hasattr(spider, "crawl_job_id")
        assert isinstance(spider.crawl_job_id, str)
        assert ot.get_crawl_job_id() == spider.crawl_job_id
        assert ot.get_spider_name() == "scout"
        assert ext._crawl_spans == {}

    def test_spider_opened_honors_existing_crawl_job_id(self, monkeypatch):
        monkeypatch.setenv("CRAWL_JOB_ID", "from-env")
        ext = ot.OtelTracingExtension()
        spider = SimpleNamespace(name="scout", crawl_job_id="already-set")

        ext.spider_opened(spider)

        assert spider.crawl_job_id == "already-set"
        assert ot.get_crawl_job_id() == "already-set"

    def test_spider_closed_safe_when_no_span(self):
        ext = ot.OtelTracingExtension()
        spider = SimpleNamespace(name="scout", crawl_job_id="x")
        # Must not raise
        ext.spider_closed(spider, reason="finished")

    def test_from_crawler_registers_when_otel_unset(self):
        crawler = MagicMock()
        crawler.settings.getbool.return_value = True
        crawler.settings.get.return_value = "scrapy-pipeline"

        with patch("scrapy.signals") as signals_mod:
            signals_mod.spider_opened = object()
            signals_mod.spider_closed = object()
            ext = ot.OtelTracingExtension.from_crawler(crawler)

        assert isinstance(ext, ot.OtelTracingExtension)
        assert crawler.signals.connect.call_count == 2
        assert ot._enabled is False
