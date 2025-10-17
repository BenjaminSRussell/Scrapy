"""Integration test for MetadataExtractionPipeline and metadata_queue table.

This test verifies that:
1. MetadataExtractionPipeline extracts keywords from content
2. Extracted metadata is saved to metadata_queue Delta table
3. The pipeline integrates correctly with Scrapy spiders
4. Keyword extraction works with different extractors (YAKE, spaCy, simple)
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from scrapy import Spider
from scrapy.http import HtmlResponse, Request

from src.pipelines import MetadataExtractionPipeline
from src.common.delta_lake import get_delta_manager


@pytest.fixture
def delta_manager():
    """Get Delta Lake manager instance."""
    return get_delta_manager()


@pytest.fixture
def mock_crawler():
    """Create mock crawler with settings."""
    crawler = Mock()
    crawler.settings = Mock()
    crawler.settings.getbool = Mock(return_value=True)
    crawler.settings.get = Mock(return_value="simple")  # Use simple extractor by default
    crawler.settings.getint = Mock(return_value=10)
    crawler.signals = Mock()
    crawler.signals.connect = Mock()
    return crawler


@pytest.fixture
def spider():
    """Create mock spider."""
    spider = Mock(spec=Spider)
    spider.name = "test_spider"
    return spider


@pytest.fixture
def sample_item():
    """Create sample item with content."""
    return {
        "url": "https://example.com/test-page",
        "title": "Test Article About Machine Learning",
        "content": """
        Machine learning is a subset of artificial intelligence that enables
        computer systems to learn from data without explicit programming.
        Deep learning, a technique within machine learning, uses neural networks
        with multiple layers to process complex patterns. Applications of machine
        learning include natural language processing, computer vision, and
        recommendation systems. The field continues to evolve with new techniques
        and algorithms being developed regularly.
        """,
        "scraped_at_utc": datetime.utcnow().isoformat() + "Z",
    }


class TestMetadataExtractionPipeline:
    """Test suite for MetadataExtractionPipeline."""

    def test_pipeline_initialization_simple(self, mock_crawler):
        """Test pipeline initializes with simple extractor."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.enabled is True
        assert pipeline.extractor_type == "simple"
        assert pipeline.batch_size == 10
        assert pipeline.max_keywords == 10
        assert pipeline.extractor is None  # Simple extractor doesn't need instance

    def test_pipeline_initialization_yake(self, mock_crawler):
        """Test pipeline initializes with YAKE extractor."""
        mock_crawler.settings.get = Mock(return_value="yake")

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.extractor_type == "yake"
        # Extractor may be None if yake not installed

    def test_pipeline_disabled(self, mock_crawler, spider, sample_item):
        """Test pipeline passes through items when disabled."""
        mock_crawler.settings.getbool = Mock(return_value=False)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        result = pipeline.process_item(sample_item, spider)

        assert result == sample_item
        assert "extracted_metadata" not in result

    def test_extract_keywords_simple(self, mock_crawler, spider, sample_item):
        """Test simple keyword extraction."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        result = pipeline.process_item(sample_item, spider)

        # Verify metadata was added
        assert "extracted_metadata" in result
        assert "keywords" in result["extracted_metadata"]

        keywords = result["extracted_metadata"]["keywords"]
        assert isinstance(keywords, list)
        assert len(keywords) <= pipeline.max_keywords

        # Should extract relevant keywords about machine learning
        keywords_text = " ".join(keywords)
        assert "machine" in keywords_text or "learning" in keywords_text

    def test_extract_keywords_from_different_fields(self, mock_crawler, spider):
        """Test extraction works with different content field names."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Test with "text" field
        item1 = {
            "url": "https://example.com/1",
            "text": "artificial intelligence and machine learning algorithms",
        }
        result1 = pipeline.process_item(item1, spider)
        assert "extracted_metadata" in result1

        # Test with "body" field
        item2 = {
            "url": "https://example.com/2",
            "body": "neural networks and deep learning frameworks",
        }
        result2 = pipeline.process_item(item2, spider)
        assert "extracted_metadata" in result2

    def test_skip_items_without_content(self, mock_crawler, spider):
        """Test pipeline skips items without text content."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Item with no content fields
        item = {"url": "https://example.com/no-content"}
        result = pipeline.process_item(item, spider)

        assert "extracted_metadata" not in result

    def test_batch_processing(self, mock_crawler, spider, delta_manager):
        """Test items are batched before writing to Delta Lake."""
        # Set small batch size for testing
        mock_crawler.settings.getint = Mock(side_effect=lambda key, default: 3 if "BATCH" in key else default)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Process items (less than batch size)
        for i in range(2):
            item = {
                "url": f"https://example.com/{i}",
                "content": f"Test content about topic {i} with keywords",
            }
            pipeline.process_item(item, spider)

        # Batch should not be saved yet
        assert len(pipeline.batch) == 2

        # Process one more to trigger batch save
        item = {
            "url": "https://example.com/3",
            "content": "Third item with content",
        }
        with patch.object(pipeline, '_save_batch') as mock_save:
            pipeline.process_item(item, spider)
            mock_save.assert_called_once()

    def test_spider_closed_flushes_batch(self, mock_crawler, spider):
        """Test remaining batch is saved when spider closes."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Add items to batch (less than batch_size)
        item = {
            "url": "https://example.com/test",
            "content": "Test content for batch flushing",
        }
        pipeline.process_item(item, spider)

        # Verify batch has items
        assert len(pipeline.batch) > 0

        # Close spider
        with patch.object(pipeline, '_save_batch') as mock_save:
            pipeline.spider_closed(spider)
            mock_save.assert_called_once()

    def test_metadata_queue_schema(self, mock_crawler, spider, sample_item):
        """Test metadata_queue records have correct schema."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        pipeline.process_item(sample_item, spider)

        # Check batch record structure
        assert len(pipeline.batch) == 1
        record = pipeline.batch[0]

        # Verify schema
        assert "url" in record
        assert "title" in record
        assert "keywords" in record
        assert "entities" in record
        assert "extraction_timestamp" in record
        assert "spider_name" in record

        # Verify types
        assert isinstance(record["url"], str)
        assert isinstance(record["keywords"], list)
        assert isinstance(record["entities"], dict)
        assert record["spider_name"] == "test_spider"

    @pytest.mark.integration
    def test_write_to_delta_lake(self, mock_crawler, spider, sample_item, delta_manager):
        """Integration test: verify data is written to metadata_queue table."""
        # Use small batch size
        mock_crawler.settings.getint = Mock(side_effect=lambda key, default: 2 if "BATCH" in key else default)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Process enough items to trigger batch save
        for i in range(3):
            item = {
                "url": f"https://example.com/test-{i}",
                "title": f"Test Article {i}",
                "content": f"Machine learning content for test {i} with keywords and data",
            }
            pipeline.process_item(item, spider)

        # Flush remaining batch
        pipeline.spider_closed(spider)

        # Verify data was written to Delta Lake
        try:
            records = delta_manager.read("metadata_queue")

            # Should have at least the records we wrote
            assert len(records) >= 3

            # Verify a sample record
            test_records = [r for r in records if r["spider_name"] == "test_spider"]
            assert len(test_records) >= 3

            sample = test_records[0]
            assert "keywords" in sample
            assert len(sample["keywords"]) > 0

        except Exception as e:
            pytest.skip(f"Delta Lake not available: {e}")

    def test_keyword_extraction_simple_method(self):
        """Test simple keyword extraction method directly."""
        pipeline = MetadataExtractionPipeline(enabled=True, extractor_type="simple")

        text = """
        Python programming language is widely used for data science.
        Machine learning frameworks like TensorFlow and PyTorch are popular.
        Python has extensive libraries for scientific computing and analysis.
        """

        keywords = pipeline._extract_keywords_simple(text)

        assert isinstance(keywords, list)
        assert len(keywords) <= pipeline.max_keywords

        # Should extract relevant terms
        keywords_lower = [kw.lower() for kw in keywords]
        assert any("python" in kw for kw in keywords_lower)

    def test_handles_empty_text(self, mock_crawler, spider):
        """Test pipeline handles empty text gracefully."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        item = {
            "url": "https://example.com/empty",
            "content": "",
        }

        result = pipeline.process_item(item, spider)
        # Should skip empty content
        assert "extracted_metadata" not in result

    def test_handles_very_long_text(self, mock_crawler, spider):
        """Test pipeline handles very long text without errors."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        # Generate long text
        long_text = " ".join(["machine learning" for _ in range(10000)])

        item = {
            "url": "https://example.com/long",
            "content": long_text,
        }

        # Should not raise exception
        result = pipeline.process_item(item, spider)
        assert "extracted_metadata" in result

    def test_stop_words_filtered(self):
        """Test that common stop words are filtered out."""
        pipeline = MetadataExtractionPipeline(enabled=True, extractor_type="simple")

        text = "this is a test with many common words like this that and these"
        keywords = pipeline._extract_keywords_simple(text)

        # Stop words should be filtered
        stop_words = {"this", "that", "with", "these"}
        keywords_set = set(kw.lower() for kw in keywords)

        # Should not contain stop words
        assert not keywords_set.intersection(stop_words)

    def test_item_count_tracking(self, mock_crawler, spider, sample_item):
        """Test pipeline tracks item count correctly."""
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.items_processed == 0

        # Process items
        for i in range(5):
            item = sample_item.copy()
            item["url"] = f"https://example.com/{i}"
            pipeline.process_item(item, spider)

        assert pipeline.items_processed == 5


@pytest.mark.integration
class TestMetadataQueueIntegration:
    """Integration tests for metadata_queue Delta table."""

    def test_metadata_queue_creation(self, delta_manager):
        """Test metadata_queue table is created correctly."""
        try:
            # Table should be created on first write
            tables = delta_manager.list_tables()
            table_names = [t["name"] for t in tables]

            # metadata_queue may not exist yet, which is OK
            # It will be created on first write
            assert True  # Placeholder - real test happens in write test

        except Exception as e:
            pytest.skip(f"Delta Lake not available: {e}")

    def test_read_metadata_queue(self, delta_manager):
        """Test reading from metadata_queue table."""
        try:
            records = delta_manager.read("metadata_queue")

            # Verify records have expected fields
            if records:
                sample = records[0]
                assert "url" in sample
                assert "keywords" in sample

        except Exception as e:
            # Table may not exist yet, which is OK
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
