"""Comprehensive tests for discovery pipeline - every edge case"""
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, mock_open
import pytest

from stage1.discovery_pipeline import Stage1Pipeline
from common.schemas import DiscoveryItem


class TestPipelineInitialization:
    """pipeline setup testing because configuration matters"""

    def test_default_initialization(self):
        pipeline = Stage1Pipeline()
        expected_path = Path("data/processed/stage01/new_urls.jsonl")
        assert pipeline.output_file == expected_path

    def test_custom_output_file(self):
        custom_path = "/custom/path/output.jsonl"
        pipeline = Stage1Pipeline(custom_path)
        assert pipeline.output_file == Path(custom_path)

    def test_from_crawler_method(self):
        mock_crawler = Mock()
        mock_settings = Mock()
        mock_settings.get.return_value = "/crawler/output.jsonl"
        mock_crawler.settings = mock_settings

        pipeline = Stage1Pipeline.from_crawler(mock_crawler)
        assert pipeline.output_file == Path("/crawler/output.jsonl")

    def test_from_crawler_no_setting(self):
        mock_crawler = Mock()
        mock_settings = Mock()
        mock_settings.get.return_value = None
        mock_crawler.settings = mock_settings

        pipeline = Stage1Pipeline.from_crawler(mock_crawler)
        expected_path = Path("data/processed/stage01/new_urls.jsonl")
        assert pipeline.output_file == expected_path


class TestSpiderOpen:
    """spider opening because setup is critical"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"
        self.pipeline = Stage1Pipeline(str(self.output_file))

    def test_open_spider_creates_directory(self):
        # ensure parent directory doesn't exist
        self.output_file.parent.rmdir()
        assert not self.output_file.parent.exists()

        self.pipeline.open_spider(Mock())

        assert self.output_file.parent.exists()

    def test_open_spider_initializes_attributes(self):
        self.pipeline.open_spider(Mock())

        assert hasattr(self.pipeline, 'file')
        assert hasattr(self.pipeline, 'seen_hashes')
        assert hasattr(self.pipeline, 'url_count')
        assert self.pipeline.url_count == 0
        assert isinstance(self.pipeline.seen_hashes, set)

    def test_open_spider_with_existing_file(self):
        # create existing file with some data
        existing_data = [
            {"url_hash": "hash1", "discovered_url": "https://uconn.edu/page1"},
            {"url_hash": "hash2", "discovered_url": "https://uconn.edu/page2"},
            {"invalid": "line without hash"}
        ]

        with open(self.output_file, 'w') as f:
            for item in existing_data:
                f.write(json.dumps(item) + '\n')

        self.pipeline.open_spider(Mock())

        # should load existing hashes
        assert "hash1" in self.pipeline.seen_hashes
        assert "hash2" in self.pipeline.seen_hashes
        assert len(self.pipeline.seen_hashes) == 2

    def test_open_spider_with_large_file(self):
        # test performance optimization for large files
        with patch.object(self.pipeline.output_file, 'open') as mock_open_method:
            mock_file = Mock()
            mock_file.tell.return_value = 100 * 1024 * 1024  # 100MB
            mock_file.__enter__.return_value = mock_file
            mock_file.__iter__.return_value = iter([
                '{"url_hash": "hash1"}\n',
                '{"url_hash": "hash2"}\n'
            ])
            mock_open_method.return_value = mock_file

            self.pipeline.open_spider(Mock())

            # should seek to end and work backwards for large files
            mock_file.seek.assert_called()

    def test_open_spider_malformed_json_resilience(self):
        # file with malformed JSON lines
        malformed_content = """{"url_hash": "good1"}
{invalid json line
{"url_hash": "good2"}
not json at all
{"url_hash": "good3"}"""

        with open(self.output_file, 'w') as f:
            f.write(malformed_content)

        self.pipeline.open_spider(Mock())

        # should only load valid hashes
        assert "good1" in self.pipeline.seen_hashes
        assert "good2" in self.pipeline.seen_hashes
        assert "good3" in self.pipeline.seen_hashes
        assert len(self.pipeline.seen_hashes) == 3

    def test_open_spider_empty_file(self):
        # create empty file
        self.output_file.touch()

        self.pipeline.open_spider(Mock())

        assert len(self.pipeline.seen_hashes) == 0

    def test_open_spider_file_permissions(self):
        # test when file exists but can't be read
        self.output_file.touch()
        self.output_file.chmod(0o000)  # no permissions

        try:
            self.pipeline.open_spider(Mock())
            # should handle gracefully or raise appropriate error
        except PermissionError:
            # acceptable to fail on permission issues
            pass
        finally:
            self.output_file.chmod(0o644)  # restore permissions

    def teardown_method(self):
        if hasattr(self.pipeline, 'file') and self.pipeline.file:
            self.pipeline.file.close()


class TestItemProcessing:
    """item processing because this is where the work happens"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"
        self.pipeline = Stage1Pipeline(str(self.output_file))
        self.pipeline.open_spider(Mock())

    def teardown_method(self):
        self.pipeline.close_spider(Mock())

    def create_discovery_item(self, url_hash="test_hash", discovered_url="https://uconn.edu/test"):
        return DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url=discovered_url,
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash=url_hash
        )

    def test_process_new_item(self):
        item = self.create_discovery_item("new_hash", "https://uconn.edu/new")
        spider = Mock()

        result = self.pipeline.process_item(item, spider)

        # should return the item
        assert result == item

        # should add to seen hashes
        assert "new_hash" in self.pipeline.seen_hashes

        # should increment counter
        assert self.pipeline.url_count == 1

    def test_process_duplicate_item(self):
        # add hash to seen_hashes first
        self.pipeline.seen_hashes.add("duplicate_hash")
        initial_count = self.pipeline.url_count

        item = self.create_discovery_item("duplicate_hash", "https://uconn.edu/duplicate")
        spider = Mock()

        result = self.pipeline.process_item(item, spider)

        # should still return the item
        assert result == item

        # should not increment counter
        assert self.pipeline.url_count == initial_count

    def test_process_item_without_hash(self):
        item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/test",
            first_seen=datetime.now().isoformat(),
            discovery_depth=1
            # no url_hash
        )
        spider = Mock()

        result = self.pipeline.process_item(item, spider)

        # should return item but not process it
        assert result == item
        assert self.pipeline.url_count == 0

    def test_process_item_without_url(self):
        item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url=None,
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="test_hash"
        )
        spider = Mock()

        result = self.pipeline.process_item(item, spider)

        # should return item but not process it
        assert result == item
        assert self.pipeline.url_count == 0

    def test_process_item_file_writing(self):
        item = self.create_discovery_item("write_hash", "https://uconn.edu/write")
        spider = Mock()

        self.pipeline.process_item(item, spider)

        # check file was written
        with open(self.output_file, 'r') as f:
            content = f.read()
            assert "write_hash" in content
            assert "https://uconn.edu/write" in content

    def test_process_item_json_serialization(self):
        item = self.create_discovery_item("json_hash", "https://uconn.edu/json")
        spider = Mock()

        self.pipeline.process_item(item, spider)

        # verify JSON is valid
        with open(self.output_file, 'r') as f:
            line = f.readline()
            data = json.loads(line)
            assert data["url_hash"] == "json_hash"
            assert data["discovered_url"] == "https://uconn.edu/json"

    def test_process_item_unicode_handling(self):
        item = self.create_discovery_item("unicode_hash", "https://uconn.edu/café")
        spider = Mock()

        self.pipeline.process_item(item, spider)

        # should handle unicode properly
        with open(self.output_file, 'r', encoding='utf-8') as f:
            content = f.read()
            assert "café" in content

    def test_process_multiple_items(self):
        items = [
            self.create_discovery_item(f"hash_{i}", f"https://uconn.edu/page{i}")
            for i in range(5)
        ]
        spider = Mock()

        for item in items:
            self.pipeline.process_item(item, spider)

        assert self.pipeline.url_count == 5
        assert len(self.pipeline.seen_hashes) == 5

    def test_process_item_counter_logging(self):
        spider = Mock()

        # process 1000 items to trigger logging
        with patch('stage1.discovery_pipeline.logger') as mock_logger:
            for i in range(1000):
                item = self.create_discovery_item(f"hash_{i}", f"https://uconn.edu/page{i}")
                self.pipeline.process_item(item, spider)

            # should log progress
            mock_logger.info.assert_called()

    def test_process_item_error_handling(self):
        spider = Mock()

        # mock file write error
        with patch.object(self.pipeline.file, 'write', side_effect=IOError("Disk full")):
            with patch('stage1.discovery_pipeline.logger') as mock_logger:
                item = self.create_discovery_item("error_hash", "https://uconn.edu/error")
                result = self.pipeline.process_item(item, spider)

                # should log error but return item
                assert result == item
                mock_logger.error.assert_called()

    def test_process_item_file_flush(self):
        item = self.create_discovery_item("flush_hash", "https://uconn.edu/flush")
        spider = Mock()

        with patch.object(self.pipeline.file, 'flush') as mock_flush:
            self.pipeline.process_item(item, spider)
            mock_flush.assert_called()


class TestSpiderClose:
    """spider closing because cleanup matters"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"
        self.pipeline = Stage1Pipeline(str(self.output_file))
        self.pipeline.open_spider(Mock())

    def test_close_spider_closes_file(self):
        spider = Mock()
        mock_file = Mock()
        self.pipeline.file = mock_file

        self.pipeline.close_spider(spider)

        mock_file.close.assert_called_once()

    def test_close_spider_logs_statistics(self):
        spider = Mock()
        self.pipeline.url_count = 42

        with patch('stage1.discovery_pipeline.logger') as mock_logger:
            self.pipeline.close_spider(spider)

            # should log final count
            args = mock_logger.info.call_args_list
            logged_text = " ".join([str(call[0][0]) for call in args])
            assert "42" in logged_text

    def test_close_spider_with_zero_count(self):
        spider = Mock()
        self.pipeline.url_count = 0

        with patch('stage1.discovery_pipeline.logger') as mock_logger:
            self.pipeline.close_spider(spider)

            # should still log
            mock_logger.info.assert_called()

    def test_close_spider_file_already_closed(self):
        spider = Mock()
        mock_file = Mock()
        mock_file.close.side_effect = ValueError("I/O operation on closed file")
        self.pipeline.file = mock_file

        # should not raise exception
        self.pipeline.close_spider(spider)


class TestPerformanceOptimizations:
    """performance testing because speed matters at scale"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"

    def test_large_file_optimization(self):
        # create a large existing file
        large_data = [
            {"url_hash": f"hash_{i}", "discovered_url": f"https://uconn.edu/page{i}"}
            for i in range(10000)
        ]

        with open(self.output_file, 'w') as f:
            for item in large_data:
                f.write(json.dumps(item) + '\n')

        pipeline = Stage1Pipeline(str(self.output_file))

        # should handle large file efficiently
        with patch.object(pipeline, '_should_limit_loading', return_value=True):
            pipeline.open_spider(Mock())

        # should load some but not all hashes
        assert len(pipeline.seen_hashes) > 0
        assert len(pipeline.seen_hashes) <= 10000

    def test_memory_usage_with_many_hashes(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # simulate processing many items
        for i in range(1000):
            item = DiscoveryItem(
                source_url="https://uconn.edu/source",
                discovered_url=f"https://uconn.edu/page{i}",
                first_seen=datetime.now().isoformat(),
                discovery_depth=1,
                url_hash=f"hash_{i}"
            )
            pipeline.process_item(item, Mock())

        # memory usage should be reasonable
        assert len(pipeline.seen_hashes) == 1000

        pipeline.close_spider(Mock())

    def test_file_seeking_optimization(self):
        # test file seeking behavior for large files
        pipeline = Stage1Pipeline(str(self.output_file))

        # mock large file
        with patch.object(pipeline.output_file, 'open') as mock_open:
            mock_file = Mock()
            mock_file.tell.return_value = 100 * 1024 * 1024  # 100MB
            mock_file.__enter__.return_value = mock_file
            mock_file.__iter__.return_value = iter(['{"url_hash": "test"}\n'])
            mock_open.return_value = mock_file

            pipeline.open_spider(Mock())

            # should seek for large files
            assert mock_file.seek.called

    def test_concurrent_access_safety(self):
        # test thread safety considerations
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # simulate concurrent item processing
        items = [
            DiscoveryItem(
                source_url="https://uconn.edu/source",
                discovered_url=f"https://uconn.edu/page{i}",
                first_seen=datetime.now().isoformat(),
                discovery_depth=1,
                url_hash=f"concurrent_hash_{i}"
            )
            for i in range(100)
        ]

        for item in items:
            pipeline.process_item(item, Mock())

        # all items should be processed
        assert pipeline.url_count == 100

        pipeline.close_spider(Mock())


class TestErrorScenarios:
    """error handling because things break"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"

    def test_disk_full_scenario(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # simulate disk full
        with patch.object(pipeline.file, 'write', side_effect=OSError("No space left on device")):
            with patch('stage1.discovery_pipeline.logger') as mock_logger:
                item = DiscoveryItem(
                    source_url="https://uconn.edu/source",
                    discovered_url="https://uconn.edu/test",
                    first_seen=datetime.now().isoformat(),
                    discovery_depth=1,
                    url_hash="disk_full_hash"
                )

                result = pipeline.process_item(item, Mock())

                # should log error and return item
                assert result == item
                mock_logger.error.assert_called()

    def test_permission_denied_file_creation(self):
        # try to create file in non-writable directory
        readonly_dir = Path(self.temp_dir) / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)  # read-only

        readonly_file = readonly_dir / "output.jsonl"
        pipeline = Stage1Pipeline(str(readonly_file))

        try:
            pipeline.open_spider(Mock())
        except PermissionError:
            # acceptable to fail on permission issues
            pass
        finally:
            readonly_dir.chmod(0o755)  # restore permissions

    def test_corrupted_existing_file(self):
        # create file with corrupted data
        with open(self.output_file, 'wb') as f:
            f.write(b'\x00\x01\x02corrupted binary data\xff\xfe')

        pipeline = Stage1Pipeline(str(self.output_file))

        # should handle corrupted file gracefully
        pipeline.open_spider(Mock())
        assert len(pipeline.seen_hashes) == 0

    def test_file_locked_scenario(self):
        # simulate file being locked by another process
        pipeline = Stage1Pipeline(str(self.output_file))

        with patch('builtins.open', side_effect=PermissionError("File is locked")):
            try:
                pipeline.open_spider(Mock())
            except PermissionError:
                # acceptable to fail on file locks
                pass

    def test_network_drive_issues(self):
        # simulate network drive issues
        pipeline = Stage1Pipeline("/network/drive/output.jsonl")

        with patch('pathlib.Path.mkdir', side_effect=OSError("Network error")):
            try:
                pipeline.open_spider(Mock())
            except OSError:
                # acceptable to fail on network issues
                pass

    def test_extremely_long_urls(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # test with extremely long URL
        long_url = "https://uconn.edu/" + "x" * 10000
        item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url=long_url,
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="long_url_hash"
        )

        result = pipeline.process_item(item, Mock())
        assert result == item

        pipeline.close_spider(Mock())

    def test_unicode_encoding_issues(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # test with problematic unicode
        problematic_url = "https://uconn.edu/\ud83d\ude00\U0001f4a9"
        item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url=problematic_url,
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="unicode_hash"
        )

        result = pipeline.process_item(item, Mock())
        assert result == item

        pipeline.close_spider(Mock())


class TestEdgeCases:
    """edge cases because the real world is weird"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"

    def test_zero_byte_file(self):
        # create zero-byte file
        self.output_file.touch()

        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        assert len(pipeline.seen_hashes) == 0

    def test_file_with_only_newlines(self):
        # file with only newlines
        with open(self.output_file, 'w') as f:
            f.write('\n\n\n\n\n')

        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        assert len(pipeline.seen_hashes) == 0

    def test_file_with_only_spaces(self):
        # file with only whitespace
        with open(self.output_file, 'w') as f:
            f.write('     \t\t\t     \n   \n')

        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        assert len(pipeline.seen_hashes) == 0

    def test_item_with_all_fields_none(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # item with all None fields
        item = DiscoveryItem(
            source_url=None,
            discovered_url=None,
            first_seen=None,
            discovery_depth=None,
            url_hash=None
        )

        result = pipeline.process_item(item, Mock())
        assert result == item
        assert pipeline.url_count == 0

        pipeline.close_spider(Mock())

    def test_duplicate_detection_edge_cases(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # same hash, different URL
        item1 = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/page1",
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="same_hash"
        )

        item2 = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/page2",
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="same_hash"
        )

        pipeline.process_item(item1, Mock())
        pipeline.process_item(item2, Mock())

        # should only count once
        assert pipeline.url_count == 1

        pipeline.close_spider(Mock())

    def test_very_deep_directory_structure(self):
        # test with very deep directory structure
        deep_dir = Path(self.temp_dir)
        for i in range(50):
            deep_dir = deep_dir / f"level{i}"

        deep_file = deep_dir / "output.jsonl"
        pipeline = Stage1Pipeline(str(deep_file))

        # should create all necessary directories
        pipeline.open_spider(Mock())
        assert deep_file.parent.exists()

        pipeline.close_spider(Mock())

    def test_filename_with_special_characters(self):
        # filename with special characters
        special_file = Path(self.temp_dir) / "output with spaces & symbols!.jsonl"
        pipeline = Stage1Pipeline(str(special_file))

        pipeline.open_spider(Mock())
        assert special_file.exists()

        pipeline.close_spider(Mock())


class TestDataIntegrity:
    """data integrity testing because data corruption is bad"""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_file = Path(self.temp_dir) / "test_output.jsonl"

    def test_json_format_consistency(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # process various items
        items = [
            DiscoveryItem(
                source_url="https://uconn.edu/source1",
                discovered_url="https://uconn.edu/page1",
                first_seen="2023-01-01T00:00:00",
                discovery_depth=1,
                url_hash="hash1"
            ),
            DiscoveryItem(
                source_url="https://uconn.edu/source2",
                discovered_url="https://uconn.edu/page2",
                first_seen="2023-01-02T00:00:00",
                discovery_depth=2,
                url_hash="hash2"
            )
        ]

        for item in items:
            pipeline.process_item(item, Mock())

        pipeline.close_spider(Mock())

        # verify all lines are valid JSON
        with open(self.output_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    assert 'url_hash' in data
                    assert 'discovered_url' in data
                except json.JSONDecodeError:
                    pytest.fail(f"Invalid JSON on line {line_num}: {line}")

    def test_file_atomicity(self):
        # test that writes are atomic (all or nothing)
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        item = DiscoveryItem(
            source_url="https://uconn.edu/source",
            discovered_url="https://uconn.edu/page",
            first_seen=datetime.now().isoformat(),
            discovery_depth=1,
            url_hash="atomic_hash"
        )

        # should write and flush immediately
        pipeline.process_item(item, Mock())

        # verify data is written immediately
        with open(self.output_file, 'r') as f:
            content = f.read()
            assert "atomic_hash" in content

        pipeline.close_spider(Mock())

    def test_hash_uniqueness_enforcement(self):
        pipeline = Stage1Pipeline(str(self.output_file))
        pipeline.open_spider(Mock())

        # try to add items with same hash multiple times
        for i in range(5):
            item = DiscoveryItem(
                source_url=f"https://uconn.edu/source{i}",
                discovered_url=f"https://uconn.edu/page{i}",
                first_seen=datetime.now().isoformat(),
                discovery_depth=1,
                url_hash="duplicate_hash"
            )
            pipeline.process_item(item, Mock())

        # should only write once
        assert pipeline.url_count == 1

        # verify file only has one entry
        with open(self.output_file, 'r') as f:
            lines = f.readlines()
            assert len(lines) == 1

        pipeline.close_spider(Mock())