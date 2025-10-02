"""
Tests for async enrichment processor.
"""

import pytest
import asyncio
import aiohttp
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
import json

from src.stage3.async_enrichment import (
    AsyncEnrichmentProcessor,
    EnrichmentResult,
    AdaptiveConcurrencyController,
    run_async_enrichment
)


class TestAdaptiveConcurrencyController:
    """Test adaptive concurrency control"""

    def test_initialization(self):
        """Test controller initialization"""
        controller = AdaptiveConcurrencyController(
            initial_concurrency=10,
            min_concurrency=2,
            max_concurrency=100
        )

        assert controller.current == 10
        assert controller.min == 2
        assert controller.max == 100

    def test_record_request(self):
        """Test recording request results"""
        controller = AdaptiveConcurrencyController()

        controller.record_request(True, 100.0)
        controller.record_request(False, 500.0)

        assert len(controller._recent_requests) == 2
        assert controller.get_success_rate() == 0.5

    def test_success_rate_calculation(self):
        """Test success rate calculation"""
        controller = AdaptiveConcurrencyController()

        for _ in range(8):
            controller.record_request(True, 100.0)

        for _ in range(2):
            controller.record_request(False, 500.0)

        assert controller.get_success_rate() == 0.8

    def test_average_duration_calculation(self):
        """Test average duration calculation"""
        controller = AdaptiveConcurrencyController()

        controller.record_request(True, 100.0)
        controller.record_request(True, 200.0)
        controller.record_request(True, 300.0)

        assert controller.get_avg_duration_ms() == 200.0

    def test_concurrency_decrease_on_failure(self):
        """Test concurrency decreases on high failure rate"""
        controller = AdaptiveConcurrencyController(
            initial_concurrency=20,
            target_success_rate=0.95
        )

        # Record many failures
        for _ in range(50):
            controller.record_request(False, 1000.0)

        controller.adjust_concurrency()

        # Should decrease
        assert controller.current < 20

    def test_concurrency_increase_on_success(self):
        """Test concurrency increases on good performance"""
        controller = AdaptiveConcurrencyController(
            initial_concurrency=10,
            increase_interval=0.0  # Immediate increase for testing
        )

        # Record many successes
        for _ in range(50):
            controller.record_request(True, 100.0)

        controller.adjust_concurrency()

        # Should increase
        assert controller.current > 10

    def test_concurrency_respects_limits(self):
        """Test concurrency respects min/max limits"""
        controller = AdaptiveConcurrencyController(
            initial_concurrency=10,
            min_concurrency=5,
            max_concurrency=15
        )

        # Try to decrease below min
        for _ in range(100):
            controller.record_request(False, 1000.0)

        for _ in range(10):
            controller.adjust_concurrency()

        assert controller.current >= 5

        # Try to increase above max
        controller._recent_requests.clear()
        for _ in range(100):
            controller.record_request(True, 50.0)

        controller._last_increase_time = 0  # Force immediate increases

        for _ in range(20):
            controller.adjust_concurrency()

        assert controller.current <= 15


class TestEnrichmentResult:
    """Test enrichment result dataclass"""

    def test_initialization(self):
        """Test result initialization"""
        result = EnrichmentResult(
            url="https://example.com",
            url_hash="abc123",
            title="Test Page"
        )

        assert result.url == "https://example.com"
        assert result.url_hash == "abc123"
        assert result.title == "Test Page"
        assert result.entities == []
        assert result.keywords == []

    def test_auto_url_hash(self):
        """Test automatic URL hash generation"""
        result = EnrichmentResult(
            url="https://example.com",
            url_hash=""
        )

        assert result.url_hash != ""
        assert len(result.url_hash) == 64  # SHA256

    def test_to_dict(self):
        """Test conversion to dictionary"""
        result = EnrichmentResult(
            url="https://example.com",
            url_hash="abc123",
            title="Test"
        )

        data = result.to_dict()

        assert isinstance(data, dict)
        assert data['url'] == "https://example.com"
        assert data['title'] == "Test"


@pytest.mark.asyncio
class TestAsyncEnrichmentProcessor:
    """Test async enrichment processor"""

    async def test_initialization(self, tmp_path):
        """Test processor initialization"""
        output_file = tmp_path / "output.jsonl"

        async with AsyncEnrichmentProcessor(
            output_file=str(output_file),
            max_concurrency=10
        ) as processor:
            assert processor.output_file == output_file
            assert processor.max_retries == 2
            assert output_file.parent.exists()

    async def test_write_result(self, tmp_path):
        """Test writing results to file"""
        output_file = tmp_path / "output.jsonl"

        result = EnrichmentResult(
            url="https://example.com",
            url_hash="abc123",
            title="Test Page"
        )

        async with AsyncEnrichmentProcessor(
            output_file=str(output_file),
            max_concurrency=10
        ) as processor:
            await processor._write_result(result)

        # Verify file was written
        assert output_file.exists()

        with open(output_file) as f:
            data = json.loads(f.readline())
            assert data['url'] == "https://example.com"
            assert data['title'] == "Test Page"

    async def test_process_html_response(self, tmp_path):
        """Test processing HTML response"""
        output_file = tmp_path / "output.jsonl"

        html_content = """
        <html>
            <head><title>Test Page</title></head>
            <body>
                <p>This is test content with some text.</p>
                <a href="/link1">Link 1</a>
                <a href="/document.pdf">PDF Link</a>
            </body>
        </html>
        """

        response_data = {
            'body': html_content.encode(),
            'text': html_content,
            'status': 200,
            'content_type': 'text/html',
            'normalized_content_type': 'text/html'
        }

        async with AsyncEnrichmentProcessor(
            output_file=str(output_file),
            max_concurrency=10,
            nlp_config={'use_transformers': False}
        ) as processor:
            result = await processor._process_response(
                url="https://example.com",
                url_hash="abc123",
                response_data=response_data
            )

            assert result.url == "https://example.com"
            assert result.title == "Test Page"
            assert "test content" in result.text_content.lower()
            assert result.has_pdf_links is True
            assert result.status_code == 200

    async def test_error_handling(self, tmp_path):
        """Test error handling during fetch"""
        output_file = tmp_path / "output.jsonl"

        async with AsyncEnrichmentProcessor(
            output_file=str(output_file),
            max_concurrency=10,
            max_retries=1
        ) as processor:
            # Mock session that raises error
            mock_session = AsyncMock()
            mock_session.get.side_effect = aiohttp.ClientError("Connection failed")

            result = await processor.fetch_url(
                session=mock_session,
                url="https://example.com",
                url_hash="abc123"
            )

            assert result.error is not None
            assert "Connection failed" in result.error or "Fetch failed" in result.error

    async def test_stats_tracking(self, tmp_path):
        """Test statistics tracking"""
        output_file = tmp_path / "output.jsonl"

        async with AsyncEnrichmentProcessor(
            output_file=str(output_file),
            max_concurrency=10
        ) as processor:
            # Simulate processing
            result = EnrichmentResult(
                url="https://example.com",
                url_hash="abc123",
                fetch_duration_ms=100.0,
                process_duration_ms=50.0
            )

            await processor._write_result(result)

            processor.stats['total_processed'] = 1
            processor.stats['total_success'] = 1
            processor.stats['total_fetch_time_ms'] = 100.0
            processor.stats['total_process_time_ms'] = 50.0

            assert processor.stats['total_processed'] == 1
            assert processor.stats['total_success'] == 1


@pytest.mark.asyncio
async def test_run_async_enrichment(tmp_path):
    """Test run_async_enrichment convenience function"""
    output_file = tmp_path / "output.jsonl"

    urls = [
        "https://example.com/page1",
        "https://example.com/page2"
    ]

    # Mock the actual fetching to avoid network calls
    with patch('src.stage3.async_enrichment.AsyncEnrichmentProcessor.fetch_url') as mock_fetch:
        mock_fetch.return_value = EnrichmentResult(
            url="https://example.com/page1",
            url_hash="abc123",
            title="Test"
        )

        await run_async_enrichment(
            urls=urls,
            output_file=str(output_file),
            max_concurrency=10,
            batch_size=10
        )

        # Verify function was called
        assert mock_fetch.called


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
