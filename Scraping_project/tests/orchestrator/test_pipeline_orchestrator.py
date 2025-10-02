"""Tests for Pipeline Orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

import pytest

from orchestrator.pipeline import BatchQueue, BatchQueueItem, PipelineOrchestrator
from tests.samples import build_discovery_item, write_jsonl


def make_batch_item(index: int, stage: str = "stage1") -> BatchQueueItem:
    return BatchQueueItem(
        url=f"https://uconn.edu/page{index}",
        url_hash=f"hash_{stage}_{index}",
        source_stage=stage,
        data={"index": index},
    )


def test_batch_queue_operations():
    queue = BatchQueue(batch_size=2, max_queue_size=5)

    async def run():
        items = [make_batch_item(i) for i in range(3)]
        for item in items:
            await queue.put(item)

        first = await queue.get_batch()
        second = await queue.get_batch()
        return first, second

    first_batch, second_batch = asyncio.run(run())

    assert len(first_batch) == 2
    assert len(second_batch) == 1
    assert queue.is_empty()


def test_batch_queue_backpressure():
    queue = BatchQueue(batch_size=1, max_queue_size=2)

    async def run():
        items = [make_batch_item(i) for i in range(3)]
        for item in items[:2]:
            await queue.put(item)

        third_put = asyncio.create_task(queue.put(items[2]))
        await asyncio.sleep(0)
        pending_before = not third_put.done()
        await queue.get_batch()
        await asyncio.sleep(0)
        await third_put
        size_after = queue.qsize()
        return pending_before, size_after

    pending, size_after = asyncio.run(run())
    assert pending is True
    assert size_after == 2


class DummyConfig:
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.stage1_output = base_path / "stage01.jsonl"
        self.stage2_output = base_path / "stage02.jsonl"
        self.stage3_output = base_path / "stage03.jsonl"

    def get_stage1_config(self) -> Dict[str, Any]:
        return {
            "max_depth": 2,
            "batch_size": 100,
            "output_file": str(self.stage1_output),
        }

    def get_stage2_config(self) -> Dict[str, Any]:
        return {
            "max_workers": 4,
            "timeout": 5,
            "output_file": str(self.stage2_output),
        }

    def get_stage3_config(self) -> Dict[str, Any]:
        return {
            "nlp_enabled": False,
            "max_text_length": 2000,
            "top_keywords": 10,
            "output_file": str(self.stage3_output),
            "batch_size": 2,
        }

    def get(self, *keys: str, default=None):
        if keys == ("scrapy", "user_agent"):
            return "OrchestratorTest/1.0"
        if keys == ("logging", "level"):
            return "INFO"
        return default


def test_pipeline_orchestrator_stage_loading(tmp_path):
    config = DummyConfig(tmp_path)
    discovery_items = [
        asdict(build_discovery_item(
            source_url="https://uconn.edu/source",
            discovered_url=f"https://uconn.edu/page{i}",
            url_hash=f"hash_{i}",
            discovery_depth=1,
            first_seen=f"2024-01-01T00:00:{i:02d}"
        ))
        for i in range(5)
    ]
    write_jsonl(Path(config.get_stage1_config()["output_file"]), discovery_items)

    orchestrator = PipelineOrchestrator(config)

    async def run():
        items = []
        async for item in orchestrator.load_stage1_results():
            items.append(item)
        return items

    loaded = asyncio.run(run())

    assert len(loaded) == 5
    assert loaded[0].url.startswith("https://uconn.edu/page0")


def test_pipeline_orchestrator_queue_management(tmp_path):
    config = DummyConfig(tmp_path)
    orchestrator = PipelineOrchestrator(config)

    assert orchestrator.get_stage2_queue().batch_size == config.get_stage2_config()["max_workers"]
    assert orchestrator.get_stage3_queue().batch_size == config.get_stage3_config()["batch_size"]


def test_concurrent_producer_consumer(tmp_path):
    config = DummyConfig(tmp_path)
    validation_data = [
        asdict(build_discovery_item(
            source_url="https://uconn.edu/source",
            discovered_url=f"https://uconn.edu/page{i}",
            url_hash=f"hash_{i}",
            discovery_depth=1,
        ))
        for i in range(6)
    ]
    write_jsonl(Path(config.get_stage1_config()["output_file"]), validation_data)

    orchestrator = PipelineOrchestrator(config)
    processed: List[str] = []

    class FakeValidator:
        def __init__(self, sink: List[str]):
            self.sink = sink

        async def validate_batch(self, batch: List[BatchQueueItem]):
            for item in batch:
                self.sink.append(item.url_hash)

    asyncio.run(orchestrator.run_concurrent_stage2_validation(FakeValidator(processed)))

    assert sorted(processed) == sorted(f"hash_{i}" for i in range(6))
