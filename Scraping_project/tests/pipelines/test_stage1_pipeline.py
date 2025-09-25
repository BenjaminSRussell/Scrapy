import json
import sys
from pathlib import Path
from types import SimpleNamespace

# Add src to Python path for imports
src_dir = Path(__file__).parent.parent.parent / 'src'
sys.path.insert(0, str(src_dir))

from common.schemas import DiscoveryItem
from stage1.discovery_pipeline import Stage1Pipeline


def test_stage1_pipeline_writes_first_1000(tmp_path, first_1000_urls):
    """Stage 1 pipeline should persist the first 1000 discovery items without duplicates."""
    output_file = tmp_path / 'stage01' / 'new_urls.jsonl'
    pipeline = Stage1Pipeline(output_file=str(output_file))

    pipeline.open_spider(SimpleNamespace(name='test_spider'))

    for url, url_hash in first_1000_urls:
        item = DiscoveryItem(
            source_url=url,
            discovered_url=url,
            first_seen="2024-01-01T00:00:00",
            url_hash=url_hash,
            discovery_depth=0,
        )
        pipeline.process_item(item, None)

    pipeline.close_spider(SimpleNamespace(name='test_spider'))

    assert output_file.exists()
    lines = [json.loads(line) for line in output_file.read_text(encoding='utf-8').strip().splitlines()]
    assert len(lines) == 1000

    hashes = {entry['url_hash'] for entry in lines}
    assert len(hashes) == 1000


# TODO: Add more Stage 1 pipeline tests:
# 1. Test deduplication behavior with duplicate items
# 2. Test pipeline resume with existing JSONL file
# 3. Test error handling with malformed items
# 4. Test pipeline statistics and logging

def test_stage1_pipeline_deduplication():
    """Test that pipeline handles duplicate URL hashes correctly"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_path = f.name

    try:
        # Create pipeline with temporary output file
        pipeline = Stage1Pipeline(output_file=temp_path)

        # Create mock spider
        mock_spider = Mock()
        mock_spider.name = "test-spider"

        # Initialize pipeline
        pipeline.open_spider(mock_spider)

        # Create two items with same URL hash
        from itemadapter import ItemAdapter
        from common.schemas import DiscoveryItem

        item1 = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/page1",
            url_hash="test_hash_123",
            discovery_depth=1
        )

        item2 = DiscoveryItem(
            source_url="https://uconn.edu/source2",
            discovered_url="https://uconn.edu/page1",
            url_hash="test_hash_123",  # Same hash - should be deduplicated
            discovery_depth=1
        )

        # Process both items
        pipeline.process_item(item1, mock_spider)
        pipeline.process_item(item2, mock_spider)

        # Close pipeline
        pipeline.close_spider(mock_spider)

        # Verify only one item was written (second should be deduplicated)
        assert pipeline.url_count == 1
        assert "test_hash_123" in pipeline.seen_hashes

        # Read the file to verify content
        with open(temp_path, 'r') as f:
            lines = [line for line in f if line.strip() and not line.startswith('#')]
            assert len(lines) == 1  # Only first item should be written

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_stage1_pipeline_resume_from_existing():
    """Test pipeline can resume with existing JSONL file"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_path = f.name
        # Pre-populate file with existing data
        existing_data = {
            "source_url": "https://uconn.edu/existing",
            "discovered_url": "https://uconn.edu/existing_page",
            "url_hash": "existing_hash_123",
            "discovery_depth": 1,
            "first_seen": "2023-01-01T00:00:00"
        }
        import json
        f.write(json.dumps(existing_data) + '\n')

    try:
        # Create pipeline that should load existing hashes
        pipeline = Stage1Pipeline(output_file=temp_path)

        # Create mock spider
        mock_spider = Mock()
        mock_spider.name = "test-spider"

        # Initialize pipeline (should load existing hashes)
        pipeline.open_spider(mock_spider)

        # Verify existing hash was loaded
        assert "existing_hash_123" in pipeline.seen_hashes
        assert len(pipeline.seen_hashes) == 1

        # Try to add item with same hash (should be skipped)
        from common.schemas import DiscoveryItem

        duplicate_item = DiscoveryItem(
            source_url="https://uconn.edu/new_source",
            discovered_url="https://uconn.edu/existing_page",
            url_hash="existing_hash_123",  # Same hash as existing
            discovery_depth=2
        )

        pipeline.process_item(duplicate_item, mock_spider)

        # Should not increment url_count since it's a duplicate
        assert pipeline.url_count == 0

        pipeline.close_spider(mock_spider)

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_stage1_pipeline_error_handling():
    """Test pipeline handles malformed items gracefully"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        temp_path = f.name

    try:
        # Create pipeline
        pipeline = Stage1Pipeline(output_file=temp_path)

        # Create mock spider
        mock_spider = Mock()
        mock_spider.name = "test-spider"

        # Initialize pipeline
        pipeline.open_spider(mock_spider)

        # Create item with missing url_hash (should be handled gracefully)
        from common.schemas import DiscoveryItem

        malformed_item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/page1",
            url_hash=None,  # Missing hash
            discovery_depth=1
        )

        # Should not crash when processing malformed item
        result = pipeline.process_item(malformed_item, mock_spider)
        assert result is not None  # Item should still be returned

        # url_count should not increment for malformed items
        assert pipeline.url_count == 0

        pipeline.close_spider(mock_spider)

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)
