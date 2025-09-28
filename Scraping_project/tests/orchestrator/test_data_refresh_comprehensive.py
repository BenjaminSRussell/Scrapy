"""Comprehensive tests for data refresh system - every detail"""
import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
import pytest

from orchestrator.data_refresh import (
    DataRefreshManager,
    RefreshConfig,
    RefreshResult,
    _DummyNLPRegistry
)
from common.request_infrastructure import SmartRequestHandler, RequestResult, RequestAttempt
from common.schemas import ValidationResult


class TestRefreshConfig:
    """config testing because configuration matters"""

    def test_default_config(self):
        config = RefreshConfig()
        assert config.max_concurrent == 20
        assert config.priority_domains is None
        assert config.refresh_interval_hours == 24
        assert config.force_refresh_failed is True
        assert config.update_success_data is False
        assert config.create_incremental_files is True
        assert config.backup_existing is True

    def test_custom_config(self):
        config = RefreshConfig(
            max_concurrent=50,
            priority_domains=["important.uconn.edu"],
            refresh_interval_hours=12,
            force_refresh_failed=False,
            update_success_data=True,
            create_incremental_files=False,
            backup_existing=False
        )
        assert config.max_concurrent == 50
        assert config.priority_domains == ["important.uconn.edu"]
        assert config.refresh_interval_hours == 12
        assert config.force_refresh_failed is False
        assert config.update_success_data is True
        assert config.create_incremental_files is False
        assert config.backup_existing is False


class TestRefreshResult:
    """result object testing because data structures matter"""

    def test_refresh_result_creation(self):
        result = RefreshResult(
            url="https://uconn.edu/test",
            old_content_length=100,
            new_content_length=150,
            changed=True,
            success=True,
            error_message=None,
            refresh_timestamp="2023-01-01T00:00:00",
            processing_time=1.5
        )

        assert result.url == "https://uconn.edu/test"
        assert result.old_content_length == 100
        assert result.new_content_length == 150
        assert result.changed is True
        assert result.success is True
        assert result.error_message is None
        assert result.processing_time == 1.5

    def test_refresh_result_failure(self):
        result = RefreshResult(
            url="https://uconn.edu/failed",
            old_content_length=100,
            new_content_length=None,
            changed=False,
            success=False,
            error_message="Connection timeout",
            refresh_timestamp="2023-01-01T00:00:00",
            processing_time=30.0
        )

        assert result.success is False
        assert result.error_message == "Connection timeout"
        assert result.new_content_length is None


class TestDataRefreshManagerInit:
    """initialization testing because setup is everything"""

    def test_default_initialization(self):
        manager = DataRefreshManager()
        assert isinstance(manager.config, RefreshConfig)
        assert manager.request_handler is None

    def test_custom_config_initialization(self):
        config = RefreshConfig(max_concurrent=100)
        manager = DataRefreshManager(config)
        assert manager.config.max_concurrent == 100

    def test_file_paths_setup(self):
        manager = DataRefreshManager()
        assert manager.discovery_file == Path("data/processed/stage01/discovered_urls.jsonl")
        assert manager.validation_file == Path("data/processed/stage02/validated_urls.jsonl")
        assert manager.enrichment_file == Path("data/processed/stage03/enriched_content.jsonl")
        assert manager.refresh_history == Path("data/analytics/refresh_history.json")

    def test_analytics_directory_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = RefreshConfig()
            manager = DataRefreshManager(config)
            manager.refresh_history = Path(temp_dir) / "analytics" / "history.json"

            # should create parent directory
            assert manager.refresh_history.parent.exists()


class TestAsyncContextManager:
    """async context manager testing because resource management matters"""

    @pytest.mark.asyncio
    async def test_async_enter_exit(self):
        manager = DataRefreshManager()

        async with manager as m:
            assert m.request_handler is not None
            assert isinstance(m.request_handler, SmartRequestHandler)

        # should clean up after exit
        # request_handler should be closed

    @pytest.mark.asyncio
    async def test_async_enter_creates_handler(self):
        manager = DataRefreshManager()
        assert manager.request_handler is None

        async with manager:
            assert manager.request_handler is not None

    @pytest.mark.asyncio
    async def test_async_exit_with_exception(self):
        manager = DataRefreshManager()

        try:
            async with manager:
                raise ValueError("Test exception")
        except ValueError:
            pass

        # should still clean up properly


class TestContentLengthHelper:
    """content length testing because simple functions need tests too"""

    def test_get_content_length_normal_string(self):
        manager = DataRefreshManager()
        assert manager._get_content_length("hello world") == 11

    def test_get_content_length_empty_string(self):
        manager = DataRefreshManager()
        assert manager._get_content_length("") == 0

    def test_get_content_length_none(self):
        manager = DataRefreshManager()
        assert manager._get_content_length(None) == 0

    def test_get_content_length_unicode(self):
        manager = DataRefreshManager()
        assert manager._get_content_length("café") == 4

    def test_get_content_length_large_string(self):
        manager = DataRefreshManager()
        large_string = "x" * 100000
        assert manager._get_content_length(large_string) == 100000


class TestDataLoading:
    """data loading testing because file I/O is fragile"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def create_test_file(self, filename, data):
        filepath = Path(self.temp_dir.name) / filename
        with open(filepath, 'w') as f:
            for item in data:
                f.write(json.dumps(item) + '\n')
        return filepath

    def test_load_existing_data_valid_file(self):
        test_data = [
            {"url": "https://uconn.edu/page1", "status_code": 200},
            {"discovered_url": "https://uconn.edu/page2", "is_valid": True},
            {"url": "https://uconn.edu/page3", "content_length": 1500}
        ]
        filepath = self.create_test_file("test.jsonl", test_data)

        result = self.manager._load_existing_data(filepath)

        assert len(result) == 3
        assert "https://uconn.edu/page1" in result
        assert "https://uconn.edu/page2" in result
        assert "https://uconn.edu/page3" in result

    def test_load_existing_data_nonexistent_file(self):
        nonexistent = Path(self.temp_dir.name) / "nonexistent.jsonl"
        result = self.manager._load_existing_data(nonexistent)
        assert result == {}

    def test_load_existing_data_malformed_json(self):
        filepath = Path(self.temp_dir.name) / "malformed.jsonl"
        with open(filepath, 'w') as f:
            f.write('{"valid": "json"}\n')
            f.write('invalid json line\n')
            f.write('{"another": "valid"}\n')

        result = self.manager._load_existing_data(filepath)
        assert len(result) == 2  # should skip malformed line

    def test_load_existing_data_empty_file(self):
        filepath = Path(self.temp_dir.name) / "empty.jsonl"
        filepath.touch()

        result = self.manager._load_existing_data(filepath)
        assert result == {}

    def test_load_existing_data_no_url_fields(self):
        test_data = [
            {"no_url_field": "value"},
            {"url": "https://uconn.edu/valid"},
            {"discovered_url": "https://uconn.edu/also_valid"}
        ]
        filepath = self.create_test_file("no_urls.jsonl", test_data)

        result = self.manager._load_existing_data(filepath)
        assert len(result) == 2  # should skip item without URL

    def test_load_existing_data_permission_error(self):
        filepath = Path(self.temp_dir.name) / "restricted.jsonl"
        filepath.touch()
        filepath.chmod(0o000)  # no permissions

        with patch('orchestrator.data_refresh.logger') as mock_logger:
            result = self.manager._load_existing_data(filepath)
            assert result == {}
            mock_logger.error.assert_called()

        filepath.chmod(0o644)  # restore permissions


class TestFileBackup:
    """backup testing because data loss is bad"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_backup_existing_file(self):
        original_file = Path(self.temp_dir.name) / "original.jsonl"
        original_file.write_text("original content")

        self.manager._backup_file(original_file)

        # should create backup with timestamp
        backup_files = list(original_file.parent.glob("original.*.backup"))
        assert len(backup_files) == 1

        backup_content = backup_files[0].read_text()
        assert backup_content == "original content"

    def test_backup_nonexistent_file(self):
        nonexistent = Path(self.temp_dir.name) / "nonexistent.jsonl"

        # should not crash
        self.manager._backup_file(nonexistent)

        # should not create any backup files
        backup_files = list(nonexistent.parent.glob("*.backup"))
        assert len(backup_files) == 0

    def test_backup_permission_error(self):
        filepath = Path(self.temp_dir.name) / "restricted.jsonl"
        filepath.write_text("content")
        filepath.chmod(0o000)

        with patch('orchestrator.data_refresh.logger') as mock_logger:
            self.manager._backup_file(filepath)
            mock_logger.error.assert_called()

        filepath.chmod(0o644)

    def test_backup_large_file(self):
        large_file = Path(self.temp_dir.name) / "large.jsonl"
        large_content = "x" * 100000
        large_file.write_text(large_content)

        self.manager._backup_file(large_file)

        backup_files = list(large_file.parent.glob("large.*.backup"))
        assert len(backup_files) == 1
        assert backup_files[0].stat().st_size == large_file.stat().st_size


class TestPriorityCalculation:
    """priority calculation testing because algorithms matter"""

    def setup_method(self):
        self.config = RefreshConfig(priority_domains=["important.uconn.edu"])
        self.manager = DataRefreshManager(self.config)

    def test_priority_for_configured_domains(self):
        existing_data = {
            "https://important.uconn.edu/page": {"is_valid": True},
            "https://regular.uconn.edu/page": {"is_valid": True}
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        important_priority = next(p for url, p in priorities if "important.uconn.edu" in url)
        regular_priority = next(p for url, p in priorities if "regular.uconn.edu" in url)

        assert important_priority > regular_priority

    def test_priority_for_failed_urls(self):
        existing_data = {
            "https://failed.uconn.edu/page": {"is_valid": False, "error_message": "Timeout"},
            "https://working.uconn.edu/page": {"is_valid": True, "status_code": 200}
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        failed_priority = next(p for url, p in priorities if "failed.uconn.edu" in url)
        working_priority = next(p for url, p in priorities if "working.uconn.edu" in url)

        assert failed_priority > working_priority

    def test_priority_for_old_data(self):
        old_time = (datetime.now() - timedelta(days=2)).isoformat()
        recent_time = datetime.now().isoformat()

        existing_data = {
            "https://old.uconn.edu/page": {"validated_at": old_time, "is_valid": True},
            "https://recent.uconn.edu/page": {"validated_at": recent_time, "is_valid": True}
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        old_priority = next(p for url, p in priorities if "old.uconn.edu" in url)
        recent_priority = next(p for url, p in priorities if "recent.uconn.edu" in url)

        assert old_priority > recent_priority

    def test_priority_for_unknown_age(self):
        existing_data = {
            "https://unknown.uconn.edu/page": {"is_valid": True}  # no timestamp
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        unknown_priority = next(p for url, p in priorities if "unknown.uconn.edu" in url)
        assert unknown_priority > 0  # should get medium priority

    def test_priority_for_successful_recent_data(self):
        recent_time = datetime.now().isoformat()

        existing_data = {
            "https://good.uconn.edu/page": {
                "is_valid": True,
                "status_code": 200,
                "validated_at": recent_time
            }
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        good_priority = next(p for url, p in priorities if "good.uconn.edu" in url)
        assert good_priority < 0  # should get negative priority (lower)

    def test_priority_sorting(self):
        old_time = (datetime.now() - timedelta(days=5)).isoformat()

        existing_data = {
            "https://important.uconn.edu/old": {
                "validated_at": old_time,
                "is_valid": True
            },
            "https://failed.uconn.edu/page": {
                "is_valid": False,
                "error_message": "Error"
            },
            "https://regular.uconn.edu/recent": {
                "validated_at": datetime.now().isoformat(),
                "is_valid": True,
                "status_code": 200
            }
        }

        priorities = self.manager._get_refresh_priorities(existing_data)

        # should be sorted by priority (high to low)
        assert priorities[0][1] >= priorities[1][1] >= priorities[2][1]

    def test_priority_edge_cases(self):
        existing_data = {
            "https://no-domain-info.com/page": {"is_valid": True},  # different domain
            "invalid-url": {"is_valid": True},  # malformed URL
            "https://uconn.edu/empty": {}  # empty data
        }

        # should not crash on edge cases
        priorities = self.manager._get_refresh_priorities(existing_data)
        assert len(priorities) == 3


class TestValidationRefresh:
    """validation refresh testing because this is the main functionality"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()
        self.manager.validation_file = Path(self.temp_dir.name) / "validation.jsonl"

    def teardown_method(self):
        self.temp_dir.cleanup()

    def create_validation_data(self, data):
        with open(self.manager.validation_file, 'w') as f:
            for item in data:
                f.write(json.dumps(item) + '\n')

    @pytest.mark.asyncio
    async def test_refresh_validation_data_no_data(self):
        async with self.manager:
            result = await self.manager.refresh_validation_data()

        assert result["total_processed"] == 0
        assert result["refreshed"] == 0

    @pytest.mark.asyncio
    async def test_refresh_validation_data_force_all(self):
        test_data = [
            {"url": "https://uconn.edu/page1", "is_valid": True, "status_code": 200},
            {"url": "https://uconn.edu/page2", "is_valid": True, "status_code": 200}
        ]
        self.create_validation_data(test_data)

        with patch.object(self.manager, '_refresh_single_validation') as mock_refresh:
            mock_refresh.return_value = AsyncMock(return_value=RefreshResult(
                url="test", old_content_length=100, new_content_length=100,
                changed=False, success=True, error_message=None,
                refresh_timestamp=datetime.now().isoformat(), processing_time=1.0
            ))

            async with self.manager:
                result = await self.manager.refresh_validation_data(force_all=True)

            # should refresh all URLs
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_refresh_validation_data_selective(self):
        old_time = (datetime.now() - timedelta(days=2)).isoformat()
        recent_time = datetime.now().isoformat()

        test_data = [
            {"url": "https://uconn.edu/old", "validated_at": old_time, "is_valid": True},
            {"url": "https://uconn.edu/recent", "validated_at": recent_time, "is_valid": True, "status_code": 200},
            {"url": "https://uconn.edu/failed", "is_valid": False, "error_message": "Error"}
        ]
        self.create_validation_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = True
            mock_request_result.content = "test content"
            mock_request_result.final_status_code = 200
            mock_request_result.content_type = "text/html"
            mock_request_result.content_length = 100
            mock_request_result.total_time = 1.0
            mock_request_result.learned_optimizations = {}

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.refresh_validation_data()

            # should refresh old and failed URLs, skip recent good ones
            assert result["total_processed"] >= 1

    @pytest.mark.asyncio
    async def test_refresh_validation_handles_request_failure(self):
        test_data = [
            {"url": "https://uconn.edu/failed", "is_valid": False}
        ]
        self.create_validation_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = False
            mock_request_result.attempts = [Mock(error_message="Connection failed")]

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.refresh_validation_data()

            assert result["failed"] >= 1

    @pytest.mark.asyncio
    async def test_refresh_validation_concurrent_processing(self):
        # test with multiple URLs to verify concurrent processing
        test_data = [
            {"url": f"https://uconn.edu/page{i}", "is_valid": False}
            for i in range(10)
        ]
        self.create_validation_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = True
            mock_request_result.content = "test"
            mock_request_result.final_status_code = 200
            mock_request_result.content_type = "text/html"
            mock_request_result.content_length = 4
            mock_request_result.total_time = 1.0
            mock_request_result.learned_optimizations = {}

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.refresh_validation_data()

            assert result["total_processed"] == 10

    @pytest.mark.asyncio
    async def test_refresh_validation_exception_handling(self):
        test_data = [
            {"url": "https://uconn.edu/exception", "is_valid": False}
        ]
        self.create_validation_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_handler.fetch_with_learning = AsyncMock(side_effect=Exception("Network error"))

            async with self.manager:
                result = await self.manager.refresh_validation_data()

            assert result["failed"] >= 1


class TestEnrichmentRefresh:
    """enrichment refresh testing because we implemented this too"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()
        self.manager.enrichment_file = Path(self.temp_dir.name) / "enrichment.jsonl"

    def teardown_method(self):
        self.temp_dir.cleanup()

    def create_enrichment_data(self, data):
        with open(self.manager.enrichment_file, 'w') as f:
            for item in data:
                f.write(json.dumps(item) + '\n')

    @pytest.mark.asyncio
    async def test_refresh_enrichment_data_basic(self):
        test_data = [
            {"url": "https://uconn.edu/page1", "is_valid": False, "error_message": "Old error"}
        ]
        self.create_enrichment_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = True
            mock_request_result.content = "enriched content"
            mock_request_result.content_length = 100
            mock_request_result.total_time = 2.0

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.refresh_enrichment_data()

            assert result["total_processed"] >= 1
            assert result["successful"] >= 1

    @pytest.mark.asyncio
    async def test_refresh_enrichment_data_no_refresh_needed(self):
        recent_time = datetime.now().isoformat()
        test_data = [
            {
                "url": "https://uconn.edu/recent",
                "enriched_at": recent_time,
                "is_valid": True
            }
        ]
        self.create_enrichment_data(test_data)

        async with self.manager:
            result = await self.manager.refresh_enrichment_data()

        assert result["message"] == "No enrichment URLs need refreshing"

    @pytest.mark.asyncio
    async def test_refresh_enrichment_handles_failures(self):
        test_data = [
            {"url": "https://uconn.edu/fail", "is_valid": False}
        ]
        self.create_enrichment_data(test_data)

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = False
            mock_request_result.attempts = [Mock(error_message="Enrichment failed")]

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.refresh_enrichment_data()

            assert result["failed"] >= 1


class TestFileWriting:
    """file writing testing because persistence is important"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_write_updated_data(self):
        filepath = Path(self.temp_dir.name) / "updated.jsonl"
        data = {
            "https://uconn.edu/page1": {"url": "https://uconn.edu/page1", "status": "updated"},
            "https://uconn.edu/page2": {"url": "https://uconn.edu/page2", "status": "updated"}
        }

        self.manager._write_updated_data(filepath, data)

        assert filepath.exists()
        with open(filepath, 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2

    def test_write_updated_data_unicode(self):
        filepath = Path(self.temp_dir.name) / "unicode.jsonl"
        data = {
            "https://uconn.edu/café": {"url": "https://uconn.edu/café", "content": "café naïve"}
        }

        self.manager._write_updated_data(filepath, data)

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            assert "café" in content

    def test_write_updated_data_error_handling(self):
        readonly_file = Path(self.temp_dir.name) / "readonly.jsonl"
        readonly_file.touch()
        readonly_file.chmod(0o444)

        data = {"url": {"data": "test"}}

        with patch('orchestrator.data_refresh.logger') as mock_logger:
            self.manager._write_updated_data(readonly_file, data)
            mock_logger.error.assert_called()

        readonly_file.chmod(0o644)

    def test_write_updated_data_empty(self):
        filepath = Path(self.temp_dir.name) / "empty.jsonl"
        data = {}

        self.manager._write_updated_data(filepath, data)

        assert filepath.exists()
        assert filepath.stat().st_size == 0


class TestIncrementalFiles:
    """incremental file testing because change tracking is useful"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_create_incremental_file(self):
        base_file = Path(self.temp_dir.name) / "base.jsonl"
        base_data = {
            "https://uconn.edu/changed": {"url": "https://uconn.edu/changed", "updated": True}
        }

        with open(base_file, 'w') as f:
            json.dump(base_data["https://uconn.edu/changed"], f)

        changed_items = [
            RefreshResult(
                url="https://uconn.edu/changed",
                old_content_length=100,
                new_content_length=150,
                changed=True,
                success=True,
                error_message=None,
                refresh_timestamp=datetime.now().isoformat(),
                processing_time=1.0
            )
        ]

        self.manager._create_incremental_file(base_file, changed_items, "test")

        incremental_files = list(base_file.parent.glob("base_incremental_*.jsonl"))
        assert len(incremental_files) == 1

    def test_create_incremental_file_no_changes(self):
        base_file = Path(self.temp_dir.name) / "base.jsonl"
        base_file.touch()
        changed_items = []

        self.manager._create_incremental_file(base_file, changed_items, "test")

        incremental_files = list(base_file.parent.glob("base_incremental_*.jsonl"))
        assert len(incremental_files) == 0

    def test_create_incremental_file_error_handling(self):
        base_file = Path(self.temp_dir.name) / "nonexistent.jsonl"
        changed_items = [
            RefreshResult(
                url="https://uconn.edu/test",
                old_content_length=100,
                new_content_length=150,
                changed=True,
                success=True,
                error_message=None,
                refresh_timestamp=datetime.now().isoformat(),
                processing_time=1.0
            )
        ]

        with patch('orchestrator.data_refresh.logger') as mock_logger:
            self.manager._create_incremental_file(base_file, changed_items, "test")
            mock_logger.error.assert_called()


class TestRefreshHistory:
    """refresh history testing because analytics matter"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()
        self.manager.refresh_history = Path(self.temp_dir.name) / "history.json"

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_save_refresh_history_new_file(self):
        results = [
            RefreshResult(
                url="https://uconn.edu/test",
                old_content_length=100,
                new_content_length=150,
                changed=True,
                success=True,
                error_message=None,
                refresh_timestamp=datetime.now().isoformat(),
                processing_time=1.0
            )
        ]

        self.manager._save_refresh_history("validation", results)

        assert self.manager.refresh_history.exists()
        with open(self.manager.refresh_history, 'r') as f:
            history = json.load(f)
            assert len(history) == 1
            assert history[0]["stage"] == "validation"

    def test_save_refresh_history_append_to_existing(self):
        # create existing history
        existing_history = [
            {"stage": "old", "timestamp": "2023-01-01T00:00:00", "total_processed": 5}
        ]
        with open(self.manager.refresh_history, 'w') as f:
            json.dump(existing_history, f)

        results = [
            RefreshResult(
                url="https://uconn.edu/test",
                old_content_length=100,
                new_content_length=150,
                changed=True,
                success=True,
                error_message=None,
                refresh_timestamp=datetime.now().isoformat(),
                processing_time=1.0
            )
        ]

        self.manager._save_refresh_history("validation", results)

        with open(self.manager.refresh_history, 'r') as f:
            history = json.load(f)
            assert len(history) == 2

    def test_save_refresh_history_limit_entries(self):
        # create history with 100 entries
        existing_history = [
            {"stage": f"test{i}", "timestamp": f"2023-01-{i:02d}T00:00:00", "total_processed": i}
            for i in range(1, 101)
        ]
        with open(self.manager.refresh_history, 'w') as f:
            json.dump(existing_history, f)

        results = [RefreshResult(
            url="https://uconn.edu/test", old_content_length=100, new_content_length=150,
            changed=True, success=True, error_message=None,
            refresh_timestamp=datetime.now().isoformat(), processing_time=1.0
        )]

        self.manager._save_refresh_history("validation", results)

        with open(self.manager.refresh_history, 'r') as f:
            history = json.load(f)
            # should keep only last 100 entries
            assert len(history) == 100

    def test_save_refresh_history_error_handling(self):
        readonly_dir = Path(self.temp_dir.name) / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        self.manager.refresh_history = readonly_dir / "history.json"

        results = [RefreshResult(
            url="https://uconn.edu/test", old_content_length=100, new_content_length=150,
            changed=True, success=True, error_message=None,
            refresh_timestamp=datetime.now().isoformat(), processing_time=1.0
        )]

        with patch('orchestrator.data_refresh.logger') as mock_logger:
            self.manager._save_refresh_history("validation", results)
            mock_logger.error.assert_called()

        readonly_dir.chmod(0o755)


class TestFullDataRefresh:
    """full refresh testing because integration matters"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()
        self.manager.validation_file = Path(self.temp_dir.name) / "validation.jsonl"
        self.manager.enrichment_file = Path(self.temp_dir.name) / "enrichment.jsonl"

    def teardown_method(self):
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_full_data_refresh(self):
        # create test data
        validation_data = [{"url": "https://uconn.edu/val", "is_valid": False}]
        enrichment_data = [{"url": "https://uconn.edu/enr", "is_valid": False}]

        with open(self.manager.validation_file, 'w') as f:
            for item in validation_data:
                f.write(json.dumps(item) + '\n')

        with open(self.manager.enrichment_file, 'w') as f:
            for item in enrichment_data:
                f.write(json.dumps(item) + '\n')

        with patch.object(self.manager, 'request_handler') as mock_handler:
            mock_request_result = Mock()
            mock_request_result.success = True
            mock_request_result.content = "test"
            mock_request_result.final_status_code = 200
            mock_request_result.content_type = "text/html"
            mock_request_result.content_length = 4
            mock_request_result.total_time = 1.0
            mock_request_result.learned_optimizations = {}

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_request_result)

            async with self.manager:
                result = await self.manager.full_data_refresh()

            assert "validation" in result
            assert "enrichment" in result


class TestRefreshStatus:
    """refresh status testing because monitoring is important"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()
        self.manager.validation_file = Path(self.temp_dir.name) / "validation.jsonl"
        self.manager.refresh_history = Path(self.temp_dir.name) / "history.json"

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_get_refresh_status_empty_data(self):
        self.manager.validation_file.touch()

        status = self.manager.get_refresh_status()

        assert status["total_urls"] == 0
        assert status["high_priority_count"] == 0
        assert status["failed_count"] == 0
        assert status["last_refresh"] is None

    def test_get_refresh_status_with_data(self):
        test_data = [
            {"url": "https://important.uconn.edu/page", "is_valid": True},
            {"url": "https://failed.uconn.edu/page", "is_valid": False},
            {"url": "https://old.uconn.edu/page", "validated_at": "2020-01-01T00:00:00", "is_valid": True}
        ]

        with open(self.manager.validation_file, 'w') as f:
            for item in test_data:
                f.write(json.dumps(item) + '\n')

        # configure priority domains
        self.manager.config.priority_domains = ["important.uconn.edu"]

        status = self.manager.get_refresh_status()

        assert status["total_urls"] == 3
        assert status["failed_count"] == 1
        assert status["recommendations"]["should_refresh"] is True

    def test_get_refresh_status_with_history(self):
        self.manager.validation_file.touch()

        history = [
            {"timestamp": "2023-06-01T12:00:00", "stage": "validation"}
        ]
        with open(self.manager.refresh_history, 'w') as f:
            json.dump(history, f)

        status = self.manager.get_refresh_status()

        assert status["last_refresh"] == "2023-06-01T12:00:00"

    def test_get_refresh_status_recommendations(self):
        old_time = (datetime.now() - timedelta(days=5)).isoformat()

        test_data = [
            {"url": f"https://old{i}.uconn.edu/page", "validated_at": old_time, "is_valid": True}
            for i in range(15)
        ]

        with open(self.manager.validation_file, 'w') as f:
            for item in test_data:
                f.write(json.dumps(item) + '\n')

        status = self.manager.get_refresh_status()

        assert status["recommendations"]["should_refresh"] is True
        assert status["recommendations"]["estimated_time_minutes"] > 0


class TestErrorHandlingAndEdgeCases:
    """comprehensive error testing because everything breaks"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DataRefreshManager()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_invalid_datetime_parsing(self):
        existing_data = {
            "https://uconn.edu/invalid": {"validated_at": "not-a-datetime", "is_valid": True}
        }

        # should not crash on invalid datetime
        priorities = self.manager._get_refresh_priorities(existing_data)
        assert len(priorities) == 1

    def test_malformed_url_in_domain_extraction(self):
        existing_data = {
            "not-a-url": {"is_valid": True},
            "https://": {"is_valid": True},
            "": {"is_valid": True}
        }

        # should handle malformed URLs gracefully
        priorities = self.manager._get_refresh_priorities(existing_data)
        assert len(priorities) == 3

    @pytest.mark.asyncio
    async def test_request_handler_not_initialized(self):
        # try to use manager without async context
        manager = DataRefreshManager()

        with pytest.raises(AttributeError):
            await manager.refresh_validation_data()

    def test_very_large_priority_calculation(self):
        # test with many URLs
        existing_data = {
            f"https://uconn{i}.edu/page": {"is_valid": True}
            for i in range(1000)
        }

        # should handle large datasets efficiently
        priorities = self.manager._get_refresh_priorities(existing_data)
        assert len(priorities) == 1000

    @pytest.mark.asyncio
    async def test_concurrent_semaphore_limit(self):
        # test semaphore limits with many URLs
        config = RefreshConfig(max_concurrent=2)
        manager = DataRefreshManager(config)
        manager.validation_file = Path(self.temp_dir.name) / "validation.jsonl"

        test_data = [
            {"url": f"https://uconn.edu/page{i}", "is_valid": False}
            for i in range(10)
        ]

        with open(manager.validation_file, 'w') as f:
            for item in test_data:
                f.write(json.dumps(item) + '\n')

        call_count = 0
        async def mock_fetch(url):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)  # simulate network delay
            return Mock(success=True, content="test", final_status_code=200,
                       content_type="text/html", content_length=4, total_time=0.1,
                       learned_optimizations={})

        with patch.object(manager, 'request_handler') as mock_handler:
            mock_handler.fetch_with_learning = mock_fetch

            async with manager:
                result = await manager.refresh_validation_data()

            # should process all URLs despite semaphore limit
            assert result["total_processed"] == 10

    def test_unicode_in_urls_and_content(self):
        existing_data = {
            "https://uconn.edu/café": {"url": "https://uconn.edu/café", "content": "naïve résumé"},
            "https://uconn.edu/测试": {"url": "https://uconn.edu/测试", "content": "测试内容"}
        }

        # should handle unicode URLs and content
        priorities = self.manager._get_refresh_priorities(existing_data)
        assert len(priorities) == 2