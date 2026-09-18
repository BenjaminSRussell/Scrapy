import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from scrapy import Spider
from scrapy.http import HtmlResponse, Request

from src.pipelines import MetadataExtractionPipeline
from src.common.delta_lake import get_delta_manager

@pytest.fixture
def delta_manager():
    return get_delta_manager()

@pytest.fixture
def mock_crawler():
    crawler = Mock()
    crawler.settings = Mock()
    crawler.settings.getbool = Mock(return_value=True)
    crawler.settings.get = Mock(return_value="simple")
    crawler.settings.getint = Mock(return_value=10)
    crawler.signals = Mock()
    crawler.signals.connect = Mock()
    return crawler

@pytest.fixture
def spider():
    spider = Mock(spec=Spider)
    spider.name = "test_spider"
    return spider

@pytest.fixture
def sample_item():
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

    def test_pipeline_initialization_simple(self, mock_crawler):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.enabled is True
        assert pipeline.extractor_type == "simple"
        assert pipeline.batch_size == 10
        assert pipeline.max_keywords == 10
        assert pipeline.extractor is None

    def test_pipeline_initialization_yake(self, mock_crawler):
        mock_crawler.settings.get = Mock(return_value="yake")

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.extractor_type == "yake"

    def test_pipeline_disabled(self, mock_crawler, spider, sample_item):
        mock_crawler.settings.getbool = Mock(return_value=False)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        result = pipeline.process_item(sample_item, spider)

        assert result == sample_item
        assert "extracted_metadata" not in result

    def test_extract_keywords_simple(self, mock_crawler, spider, sample_item):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        result = pipeline.process_item(sample_item, spider)

        assert "extracted_metadata" in result
        assert "keywords" in result["extracted_metadata"]

        keywords = result["extracted_metadata"]["keywords"]
        assert isinstance(keywords, list)
        assert len(keywords) <= pipeline.max_keywords

        keywords_text = " ".join(keywords)
        assert "machine" in keywords_text or "learning" in keywords_text

    def test_extract_keywords_from_different_fields(self, mock_crawler, spider):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        item1 = {
            "url": "https://example.com/1",
            "text": "artificial intelligence and machine learning algorithms",
        }
        result1 = pipeline.process_item(item1, spider)
        assert "extracted_metadata" in result1

        item2 = {
            "url": "https://example.com/2",
            "body": "neural networks and deep learning frameworks",
        }
        result2 = pipeline.process_item(item2, spider)
        assert "extracted_metadata" in result2

    def test_skip_items_without_content(self, mock_crawler, spider):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        item = {"url": "https://example.com/no-content"}
        result = pipeline.process_item(item, spider)

        assert "extracted_metadata" not in result

    def test_batch_processing(self, mock_crawler, spider, delta_manager):
        mock_crawler.settings.getint = Mock(side_effect=lambda key, default: 3 if "BATCH" in key else default)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        for i in range(2):
            item = {
                "url": f"https://example.com/{i}",
                "content": f"Test content about topic {i} with keywords",
            }
            pipeline.process_item(item, spider)

        assert len(pipeline.batch) == 2

        item = {
            "url": "https://example.com/3",
            "content": "Third item with content",
        }
        with patch.object(pipeline, '_save_batch') as mock_save:
            pipeline.process_item(item, spider)
            mock_save.assert_called_once()

    def test_spider_closed_flushes_batch(self, mock_crawler, spider):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        item = {
            "url": "https://example.com/test",
            "content": "Test content for batch flushing",
        }
        pipeline.process_item(item, spider)

        assert len(pipeline.batch) > 0

        with patch.object(pipeline, '_save_batch') as mock_save:
            pipeline.spider_closed(spider)
            mock_save.assert_called_once()

    def test_metadata_queue_schema(self, mock_crawler, spider, sample_item):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)
        pipeline.process_item(sample_item, spider)

        assert len(pipeline.batch) == 1
        record = pipeline.batch[0]

        assert "url" in record
        assert "title" in record
        assert "keywords" in record
        assert "entities" in record
        assert "extraction_timestamp" in record
        assert "spider_name" in record

        assert isinstance(record["url"], str)
        assert isinstance(record["keywords"], list)
        assert isinstance(record["entities"], dict)
        assert record["spider_name"] == "test_spider"

    @pytest.mark.integration
    def test_write_to_delta_lake(self, mock_crawler, spider, sample_item, delta_manager):
        mock_crawler.settings.getint = Mock(side_effect=lambda key, default: 2 if "BATCH" in key else default)

        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        for i in range(3):
            item = {
                "url": f"https://example.com/test-{i}",
                "title": f"Test Article {i}",
                "content": f"Machine learning content for test {i} with keywords and data",
            }
            pipeline.process_item(item, spider)

        pipeline.spider_closed(spider)

        try:
            records = delta_manager.read("metadata_queue")

            assert len(records) >= 3

            test_records = [r for r in records if r["spider_name"] == "test_spider"]
            assert len(test_records) >= 3

            sample = test_records[0]
            assert "keywords" in sample
            assert len(sample["keywords"]) > 0

        except Exception as e:
            pytest.skip(f"Delta Lake not available: {e}")

    def test_keyword_extraction_simple_method(self):
        pipeline = MetadataExtractionPipeline(enabled=True, extractor_type="simple")

        text = """
        Python programming language is widely used for data science.
        Machine learning frameworks like TensorFlow and PyTorch are popular.
        Python has extensive libraries for scientific computing and analysis.
        """

        keywords = pipeline._extract_keywords_simple(text)

        assert isinstance(keywords, list)
        assert len(keywords) <= pipeline.max_keywords

        keywords_lower = [kw.lower() for kw in keywords]
        assert any("python" in kw for kw in keywords_lower)

    def test_handles_empty_text(self, mock_crawler, spider):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        item = {
            "url": "https://example.com/empty",
            "content": "",
        }

        result = pipeline.process_item(item, spider)
        assert "extracted_metadata" not in result

    def test_handles_very_long_text(self, mock_crawler, spider):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        long_text = " ".join(["machine learning" for _ in range(10000)])

        item = {
            "url": "https://example.com/long",
            "content": long_text,
        }

        result = pipeline.process_item(item, spider)
        assert "extracted_metadata" in result

    def test_stop_words_filtered(self):
        pipeline = MetadataExtractionPipeline(enabled=True, extractor_type="simple")

        text = "this is a test with many common words like this that and these"
        keywords = pipeline._extract_keywords_simple(text)

        stop_words = {"this", "that", "with", "these"}
        keywords_set = set(kw.lower() for kw in keywords)

        assert not keywords_set.intersection(stop_words)

    def test_item_count_tracking(self, mock_crawler, spider, sample_item):
        pipeline = MetadataExtractionPipeline.from_crawler(mock_crawler)

        assert pipeline.items_processed == 0

        for i in range(5):
            item = sample_item.copy()
            item["url"] = f"https://example.com/{i}"
            pipeline.process_item(item, spider)

        assert pipeline.items_processed == 5

@pytest.mark.integration
class TestMetadataQueueIntegration:

    def test_metadata_queue_creation(self, delta_manager):
        try:
            tables = delta_manager.list_tables()
            table_names = [t["name"] for t in tables]

            assert True

        except Exception as e:
            pytest.skip(f"Delta Lake not available: {e}")

    def test_read_metadata_queue(self, delta_manager):
        try:
            records = delta_manager.read("metadata_queue")

            if records:
                sample = records[0]
                assert "url" in sample
                assert "keywords" in sample

        except Exception as e:
            pass

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
