"""Stage 3 orchestration tests covering enrichment execution paths."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List
from types import SimpleNamespace

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from orchestrator.pipeline import PipelineOrchestrator
from common import config_keys as keys


class _DummyConfig:
    """Minimal configuration object for orchestrator tests."""

    def __init__(self, stage2_file: Path, stage3_file: Path, temp_dir: Path):
        self._stage2_config = {
            keys.VALIDATION_MAX_WORKERS: 4,
            keys.VALIDATION_TIMEOUT: 5,
            keys.VALIDATION_OUTPUT_FILE: str(stage2_file),
        }
        self._stage3_config = {
            keys.ENRICHMENT_BATCH_SIZE: 5,
            keys.ENRICHMENT_OUTPUT_FILE: str(stage3_file),
            keys.ENRICHMENT_ALLOWED_DOMAINS: ["example.com"],
            keys.ENRICHMENT_CONTENT_TYPES: {},
            keys.ENRICHMENT_HEADLESS_BROWSER: {},
            keys.ENRICHMENT_NLP_ENABLED: False,
            keys.VALIDATION_MAX_WORKERS: 12,
            keys.ENRICHMENT_STORAGE: {},
        }
        self._nlp_config = {
            keys.NLP_SPACY_MODEL: "en_core_web_sm",
            keys.NLP_USE_TRANSFORMERS: False,
        }
        self._temp_dir = temp_dir

    def get_stage2_config(self) -> Dict[str, Any]:
        return self._stage2_config

    def get_stage3_config(self) -> Dict[str, Any]:
        return self._stage3_config

    def get_nlp_config(self) -> Dict[str, Any]:
        return self._nlp_config

    def get_data_paths(self) -> Dict[str, Path]:
        return {keys.TEMP_DIR: self._temp_dir}

    def get(self, *path: str, default: Any = None) -> Any:
        if path and path[0] == getattr(keys, "CONTENT", "content"):
            return {}
        return default


class _DummySpider:
    name = "dummy-enrichment"


class _FakeCrawlerProcess:
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.crawled_args: List[Any] = []
        self.stopped = False

    def crawl(self, spider_cls, **kwargs):
        self.crawled_args.append((spider_cls, kwargs))

    def start(self):
        # Simulate immediate crawl completion
        return None

    def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_run_scrapy_enrichment_merges_urls_and_metadata(tmp_path):
    stage2_file = tmp_path / "stage02.jsonl"
    stage3_file = tmp_path / "stage03.jsonl"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    config = _DummyConfig(stage2_file, stage3_file, temp_dir)
    orchestrator = PipelineOrchestrator(config)

    urls = [
        "https://example.com/a",
        "https://example.com/a",  # duplicate that should be ignored
        "https://example.com/b",
    ]
    validation_items = [
        {"url": "https://example.com/a", "url_hash": "hash-a", "status_code": 200},
        {"url": "https://example.com/a", "url_hash": "hash-a-new", "status_code": 201},
        {"url": "https://example.com/b", "url_hash": "hash-b", "status_code": 200},
    ]
    spider_kwargs = {
        "urls_list": ["https://example.com/existing"],
        "validation_metadata": [
            {"url": "https://example.com/existing", "url_hash": "hash-existing", "status_code": 200},
        ],
        "extra_param": "preserve-me",
    }

    captured: Dict[str, Any] = {}

    def crawler_factory(settings: Dict[str, Any]) -> _FakeCrawlerProcess:
        process = _FakeCrawlerProcess(settings)
        captured["process"] = process
        captured["settings"] = settings
        return process

    loop = asyncio.get_running_loop()
    original_run_in_executor = loop.run_in_executor

    def immediate_executor(_executor, func, *args):
        func(*args)
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    loop.run_in_executor = immediate_executor  # type: ignore[assignment]

    try:
        await orchestrator._run_scrapy_enrichment(
            urls=urls,
            spider_cls=_DummySpider,
            scrapy_settings={"STAGE3_OUTPUT_FILE": str(stage3_file)},
            spider_kwargs=spider_kwargs,
            validation_items=validation_items,
            crawler_process_factory=crawler_factory,
        )
    finally:
        loop.run_in_executor = original_run_in_executor  # type: ignore[assignment]

    process: _FakeCrawlerProcess = captured["process"]
    assert process.crawled_args, "Crawler should be invoked with spider"
    spider_cls, kwargs = process.crawled_args[0]
    assert spider_cls is _DummySpider
    assert kwargs["extra_param"] == "preserve-me"
    assert kwargs["urls_list"] == [
        "https://example.com/existing",
        "https://example.com/a",
        "https://example.com/b",
    ]
    metadata_by_url = {entry["url"]: entry for entry in kwargs["validation_metadata"]}
    assert metadata_by_url["https://example.com/a"]["status_code"] == 201
    assert metadata_by_url["https://example.com/existing"]["url_hash"] == "hash-existing"
    assert process.stopped is True
    assert captured["settings"]["STAGE3_OUTPUT_FILE"] == str(stage3_file)


@pytest.mark.asyncio
async def test_run_async_enrichment_invokes_async_processor(monkeypatch, tmp_path):
    stage2_file = tmp_path / "stage02.jsonl"
    stage3_file = tmp_path / "stage03_async.jsonl"
    temp_dir = tmp_path / "temp_async"
    temp_dir.mkdir()

    config = _DummyConfig(stage2_file, stage3_file, temp_dir)
    orchestrator = PipelineOrchestrator(config)

    recorded: Dict[str, Any] = {}

    async def fake_run_async_enrichment(**kwargs):
        recorded.update(kwargs)

    module = SimpleNamespace(run_async_enrichment=fake_run_async_enrichment)
    monkeypatch.setitem(sys.modules, 'src.stage3.async_enrichment', module)

    await orchestrator._run_async_enrichment(
        urls=["https://example.com/resource"],
        scrapy_settings={"STAGE3_OUTPUT_FILE": str(stage3_file)},
        spider_kwargs={},
    )

    assert recorded["urls"] == ["https://example.com/resource"]
    assert recorded["output_file"] == str(stage3_file)
    assert recorded["max_concurrency"] == config.get_stage3_config()[keys.VALIDATION_MAX_WORKERS]
    assert recorded["timeout"] == 30
    assert recorded["content_types_config"] == {}
    assert recorded.get("storage_config") == {}
    assert recorded.get("storage_backend") is None
    assert recorded.get("storage_options") == {}
    assert recorded.get("rotation_config") == {}
    assert recorded.get("compression_config") == {}
