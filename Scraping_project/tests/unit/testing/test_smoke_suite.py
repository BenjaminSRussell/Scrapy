"""Curated offline smoke gate (#286): ``pytest -m smoke -o addopts=``.

Covers: core imports, one spider parse, one item pipeline, one settings
assertion. The fixture-based scout parse test is also marked ``smoke``.
Keep this suite well under 60s on a CI runner.
"""
from __future__ import annotations

import importlib

import pytest
from scrapy.exceptions import DropItem

pytestmark = pytest.mark.smoke

CORE_MODULES = [
    "src.core.config",
    "src.core.schemas",
    "src.settings",
    "src.pipelines",
    "src.stage1.scout_spider",
    "src.stage1.middlewares.retry_middleware",
    "src.stage2.stage2_worker",
    "src.stage3.stage3_worker",
    "src.stage4.large_doc_processor",
    "src.lakehouse.lakehouse_manager",
    "src.utils.redis",
]


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_modules_import(module):
    importlib.import_module(module)


def test_validation_pipeline_passes_and_drops():
    from src.pipelines import DataValidationPipeline

    pipeline = DataValidationPipeline()
    spider = type("S", (), {"name": "smoke"})()
    item = {"url": "https://example.com/"}
    assert pipeline.process_item(item, spider) is item
    for bad in ({}, {"url": "  "}, {"url": None}):
        with pytest.raises(DropItem):
            pipeline.process_item(bad, spider)
    assert (pipeline.items_validated, pipeline.items_dropped) == (1, 3)


def test_settings_wire_safety_middlewares():
    from src import settings

    assert settings.BOT_NAME
    assert "src.stage1.middlewares.ssrf_middleware.SSRFGuardMiddleware" in settings.DOWNLOADER_MIDDLEWARES
    assert isinstance(settings.ROBOTSTXT_OBEY, bool)
    assert settings.ITEM_PIPELINES  # at least one pipeline configured
