import asyncio
import hashlib
import unittest.mock
from datetime import datetime
from pathlib import Path

import pytest

from src.orchestrator.pipeline import PipelineOrchestrator
from tests.orchestrator.test_pipeline_orchestrator import DummyConfig
from tests.samples import write_jsonl


@pytest.mark.asyncio
async def test_stage3_enrichment_deduplicates_metadata(tmp_path: Path, monkeypatch):
    """
    Tests that when duplicate URLs are passed to Stage 3 enrichment,
    the associated metadata is also deduplicated before being passed to the
    enrichment processor.
    """
    # 1. Setup: Create dummy config and stage 2 output with duplicate URLs
    config = DummyConfig(tmp_path)

    def create_valid_item(url: str):
        # Creates a dict that conforms to the Pydantic ValidationResult schema
        return {
            "url": url,
            "url_hash": hashlib.sha256(url.encode('utf-8')).hexdigest(),
            "status_code": 200,
            "content_type": "text/html",
            "content_length": 1234,
            "response_time": 0.1,
            "is_valid": True,
            "error_message": None,
            "validated_at": datetime.now().isoformat(),
            "last_modified": None,
            "etag": None,
            "staleness_score": 0.0,
            "cache_control": None,
            "schema_version": "2.1",
            "validation_method": "GET",
            "redirect_chain": None,
            "server_headers": None,
            "network_metadata": None,
            "learned_optimizations": None,
        }

    # URL "https://uconn.edu/page1" is duplicated.
    url1 = "https://uconn.edu/page1"
    url2 = "https://uconn.edu/page2"
    url3 = "https://uconn.edu/page3"

    item1 = create_valid_item(url1)
    item2 = create_valid_item(url2)
    item3 = create_valid_item(url1)
    item3["response_time"] = 0.2  # Make it a distinct object
    item4 = create_valid_item(url3)

    validation_items_data = [item1, item2, item3, item4]
    write_jsonl(config.stage2_output, validation_items_data)

    orchestrator = PipelineOrchestrator(config)

    # 2. Mock: Patch the internal Scrapy enrichment runner to inspect its arguments
    mock_scrapy_runner = unittest.mock.AsyncMock()
    monkeypatch.setattr(
        "src.orchestrator.pipeline.PipelineOrchestrator._run_scrapy_enrichment",
        mock_scrapy_runner
    )

    # 3. Execute: Run the concurrent stage 3 enrichment process
    await orchestrator.run_concurrent_stage3_enrichment(
        spider_cls=None,  # Not needed for this test
        scrapy_settings={},
        use_async_processor=False  # Force the Scrapy path where the bug exists
    )

    # 4. Assert: Check the arguments passed to the mocked runner
    # The test should fail here because the metadata is not yet deduplicated.
    assert mock_scrapy_runner.call_count == 1

    # Check positional and keyword arguments
    args, kwargs = mock_scrapy_runner.call_args

    # urls (deduped_urls) is at args[0]
    passed_urls = args[0]
    # validation_items is at args[4]
    passed_metadata = args[4]

    # Expected: 3 unique URLs
    assert len(passed_urls) == 3
    # Bug: The metadata list will have 4 items instead of 3. This assertion will fail.
    assert len(passed_metadata) == 3

    # Ensure the metadata corresponds to the unique URLs
    passed_metadata_urls = {item['url'] for item in passed_metadata}
    assert passed_metadata_urls == set(passed_urls)