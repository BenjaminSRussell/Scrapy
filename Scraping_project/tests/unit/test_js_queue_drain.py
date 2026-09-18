"""Unit tests for js_spider_queue drain in PipelineOrchestrator (#645)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class InMemoryBackend:
    """Minimal Fake Delta backend for queue drain unit tests."""

    def __init__(self, tables: dict | None = None):
        self.tables: dict[str, list] = {k: list(v) for k, v in (tables or {}).items()}

    def read(self, table_name: str):
        return list(self.tables.get(table_name, []))

    def write(self, table_name: str, data, mode: str = "append", async_write: bool = True):
        if mode == "overwrite":
            self.tables[table_name] = list(data)
        else:
            self.tables.setdefault(table_name, []).extend(data)
        return True


@pytest.fixture
def fake_delta():
    return InMemoryBackend(
        {
            "js_spider_queue": [
                {"url": "https://a.example/js", "status": "pending"},
                {"url": "https://b.example/js"},  # missing status => pending
                {"url": "https://c.example/done", "status": "completed"},
                {"url": "https://d.example/fail", "status": "failed"},
            ]
        }
    )


@pytest.fixture
def orchestrator(fake_delta):
    with patch("src.orchestrator.pipeline_orchestrator.get_delta", return_value=fake_delta):
        from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator(config={})
        orch.delta = fake_delta
        return orch


def test_count_js_queue_pending(orchestrator):
    assert orchestrator.count_js_queue_pending() == 2


def test_count_js_queue_pending_empty(orchestrator, fake_delta):
    fake_delta.tables["js_spider_queue"] = []
    assert orchestrator.count_js_queue_pending() == 0


def test_run_js_queue_disabled_returns_zero_without_crawl(orchestrator):
    with patch("src.orchestrator.pipeline_orchestrator.CrawlerProcess") as mock_cp:
        remaining = orchestrator.run_js_queue(enabled=False)
    assert remaining == 0
    mock_cp.assert_not_called()
    assert orchestrator.stats.stage1_js_pending == 2


def test_run_js_queue_enabled_with_pending_invokes_crawl(orchestrator, fake_delta):
    def _start():
        for item in fake_delta.tables["js_spider_queue"]:
            if item.get("status", "pending") == "pending":
                item["status"] = "completed"

    mock_process = MagicMock()
    mock_process.start.side_effect = _start

    with (
        patch(
            "src.orchestrator.pipeline_orchestrator.CrawlerProcess",
            return_value=mock_process,
        ) as mock_cp,
        patch("src.orchestrator.pipeline_orchestrator.get_project_settings") as mock_settings,
        patch(
            "src.orchestrator.pipeline_orchestrator._update_js_queue_pending_metric"
        ) as mock_metric,
    ):
        mock_settings.return_value = MagicMock()
        remaining = orchestrator.run_js_queue(enabled=True)

    mock_cp.assert_called_once()
    mock_process.crawl.assert_called_once_with("javascript")
    mock_process.start.assert_called_once()
    assert remaining == 0
    assert orchestrator.stats.stage1_js_pending == 0
    assert mock_metric.call_count >= 1


def test_run_js_queue_enabled_empty_skips_crawl(orchestrator, fake_delta):
    fake_delta.tables["js_spider_queue"] = [{"url": "https://x", "status": "completed"}]
    with patch("src.orchestrator.pipeline_orchestrator.CrawlerProcess") as mock_cp:
        remaining = orchestrator.run_js_queue(enabled=True)
    assert remaining == 0
    mock_cp.assert_not_called()


def test_resolve_enabled_arg_and_config(orchestrator):
    assert orchestrator._resolve_js_spider_enabled(enabled=False) is False
    assert orchestrator._resolve_js_spider_enabled(enabled=True) is True
    orchestrator.config = {"enable_js_spider": False}
    assert orchestrator._resolve_js_spider_enabled() is False
    orchestrator.config = {"enable_js_spider": True}
    assert orchestrator._resolve_js_spider_enabled() is True
