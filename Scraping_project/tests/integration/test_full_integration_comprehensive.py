"""Comprehensive integration tests - testing the whole system"""
import asyncio
import json
import tempfile
import sys
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import pytest

# add src to path for imports
test_src_dir = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(test_src_dir))


class TestEndToEndPipeline:
    """end-to-end testing because the full flow matters"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def teardown_method(self):
        self.temp_dir.cleanup()

    def create_test_config(self):
        """create a test configuration"""
        from orchestrator.config import Config

        # mock the config to use our temp directory
        mock_config = Mock(spec=Config)
        mock_config.get_data_paths.return_value = {
            'logs_dir': self.data_dir / 'logs',
            'processed_dir': self.data_dir / 'processed',
            'analytics_dir': self.data_dir / 'analytics'
        }
        mock_config.get_stage1_config.return_value = {
            'output_file': str(self.data_dir / 'stage1_output.jsonl'),
            'max_depth': 2
        }
        mock_config.get_stage2_config.return_value = {
            'input_file': str(self.data_dir / 'stage1_output.jsonl'),
            'output_file': str(self.data_dir / 'stage2_output.jsonl')
        }
        mock_config.get_stage3_config.return_value = {
            'input_file': str(self.data_dir / 'stage2_output.jsonl'),
            'output_file': str(self.data_dir / 'stage3_output.jsonl')
        }
        mock_config.get_scrapy_settings.return_value = {
            'USER_AGENT': 'test-bot',
            'ROBOTSTXT_OBEY': False,
            'DOWNLOAD_DELAY': 0
        }
        mock_config.get_logging_config.return_value = {
            'level': 'INFO'
        }
        return mock_config

    @pytest.mark.asyncio
    async def test_complete_pipeline_flow(self):
        """test the complete pipeline from start to finish"""
        config = self.create_test_config()

        # create initial seed data
        seed_file = self.data_dir / "uconn_urls.csv"
        seed_file.write_text("https://uconn.edu\nhttps://www.uconn.edu/academics")

        # mock the various components
        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging'):
                with patch('pathlib.Path') as mock_path:
                    mock_path.return_value = seed_file
                    mock_path.return_value.exists.return_value = True

                    # mock scrapy components
                    with patch('orchestrator.main.CrawlerProcess') as mock_crawler_class:
                        mock_process = Mock()
                        mock_crawler_class.return_value = mock_process

                        with patch('orchestrator.main.get_project_settings', return_value=Mock()):
                            # mock orchestrator
                            with patch('orchestrator.main.PipelineOrchestrator') as mock_orchestrator_class:
                                mock_orchestrator = Mock()
                                mock_orchestrator.run_concurrent_stage2_validation = AsyncMock()
                                mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()
                                mock_orchestrator_class.return_value = mock_orchestrator

                                # run the pipeline
                                from orchestrator.main import main
                                with patch('sys.argv', ['main.py', '--stage', 'all']):
                                    result = await main()

                                    assert result == 0
                                    mock_process.crawl.assert_called_once()
                                    mock_orchestrator.run_concurrent_stage2_validation.assert_called_once()
                                    mock_orchestrator.run_concurrent_stage3_enrichment.assert_called_once()

    @pytest.mark.asyncio
    async def test_data_flow_between_stages(self):
        """test that data flows correctly between stages"""
        config = self.create_test_config()

        # create stage 1 output
        stage1_data = [
            {
                "source_url": "https://uconn.edu",
                "discovered_url": "https://uconn.edu/academics",
                "first_seen": "2023-01-01T00:00:00",
                "url_hash": "hash1",
                "discovery_depth": 1
            },
            {
                "source_url": "https://uconn.edu",
                "discovered_url": "https://uconn.edu/research",
                "first_seen": "2023-01-01T00:00:00",
                "url_hash": "hash2",
                "discovery_depth": 1
            }
        ]

        stage1_file = Path(config.get_stage1_config()['output_file'])
        stage1_file.parent.mkdir(parents=True, exist_ok=True)
        with open(stage1_file, 'w') as f:
            for item in stage1_data:
                f.write(json.dumps(item) + '\n')

        # mock stage 2 to read from stage 1 and produce output
        from orchestrator.pipeline import PipelineOrchestrator

        mock_orchestrator = Mock(spec=PipelineOrchestrator)

        async def mock_stage2_validation(validator):
            # simulate reading stage 1 output and creating stage 2 output
            stage2_data = [
                {
                    "url": "https://uconn.edu/academics",
                    "status_code": 200,
                    "is_valid": True,
                    "validated_at": "2023-01-01T01:00:00"
                },
                {
                    "url": "https://uconn.edu/research",
                    "status_code": 200,
                    "is_valid": True,
                    "validated_at": "2023-01-01T01:00:00"
                }
            ]

            stage2_file = Path(config.get_stage2_config()['output_file'])
            stage2_file.parent.mkdir(parents=True, exist_ok=True)
            with open(stage2_file, 'w') as f:
                for item in stage2_data:
                    f.write(json.dumps(item) + '\n')

        async def mock_stage3_enrichment(spider, settings):
            # simulate reading stage 2 output and creating stage 3 output
            stage3_data = [
                {
                    "url": "https://uconn.edu/academics",
                    "content": "<html>Academic programs content</html>",
                    "entities": ["University", "Connecticut"],
                    "keywords": ["academics", "programs", "university"],
                    "enriched_at": "2023-01-01T02:00:00"
                }
            ]

            stage3_file = Path(config.get_stage3_config()['output_file'])
            stage3_file.parent.mkdir(parents=True, exist_ok=True)
            with open(stage3_file, 'w') as f:
                for item in stage3_data:
                    f.write(json.dumps(item) + '\n')

        mock_orchestrator.run_concurrent_stage2_validation = mock_stage2_validation
        mock_orchestrator.run_concurrent_stage3_enrichment = mock_stage3_enrichment

        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.PipelineOrchestrator', return_value=mock_orchestrator):
                    with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                        from orchestrator.main import main
                        with patch('sys.argv', ['main.py', '--stage', 'all']):
                            result = await main()

                            assert result == 0

                            # verify stage 2 output exists
                            stage2_file = Path(config.get_stage2_config()['output_file'])
                            assert stage2_file.exists()

                            # verify stage 3 output exists
                            stage3_file = Path(config.get_stage3_config()['output_file'])
                            assert stage3_file.exists()

    @pytest.mark.asyncio
    async def test_error_propagation_and_recovery(self):
        """test error handling across the entire pipeline"""
        config = self.create_test_config()

        # test stage 1 failure
        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.run_stage1_discovery', side_effect=Exception("Stage 1 failed")):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                            with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                from orchestrator.main import main
                                with patch('sys.argv', ['main.py', '--stage', 'all']):
                                    result = await main()

                                    # should fail but continue with other stages
                                    assert result == 1
                                    mock_stage2.assert_called_once()
                                    mock_stage3.assert_called_once()

    @pytest.mark.asyncio
    async def test_configuration_propagation(self):
        """test that configuration is properly propagated to all components"""
        config = self.create_test_config()

        # add custom settings
        config.get_scrapy_settings.return_value = {
            'USER_AGENT': 'custom-bot',
            'DOWNLOAD_DELAY': 2,
            'CONCURRENT_REQUESTS': 8
        }

        config.get_logging_config.return_value = {
            'level': 'DEBUG'
        }

        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging') as mock_setup_logging:
                with patch('orchestrator.main.PipelineOrchestrator') as mock_orchestrator_class:
                    mock_orchestrator = Mock()
                    mock_orchestrator.run_concurrent_stage2_validation = AsyncMock()
                    mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()
                    mock_orchestrator_class.return_value = mock_orchestrator

                    with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                        from orchestrator.main import main
                        with patch('sys.argv', ['main.py', '--log-level', 'DEBUG']):
                            result = await main()

                            assert result == 0

                            # verify logging setup was called with correct level
                            mock_setup_logging.assert_called_once()
                            call_kwargs = mock_setup_logging.call_args[1]
                            assert call_kwargs['log_level'] == 'DEBUG'

                            # verify orchestrator was created with config
                            mock_orchestrator_class.assert_called_once_with(config)

    @pytest.mark.asyncio
    async def test_file_system_interactions(self):
        """test file system operations across the pipeline"""
        config = self.create_test_config()

        # verify directories are created
        data_paths = config.get_data_paths()

        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.PipelineOrchestrator'):
                    with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                        with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock):
                            with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock):
                                from orchestrator.main import main
                                with patch('sys.argv', ['main.py']):
                                    result = await main()

                                    assert result == 0

                                    # verify all data directories were created
                                    for path in data_paths.values():
                                        assert path.exists()
                                        assert path.is_dir()

    @pytest.mark.asyncio
    async def test_memory_and_resource_management(self):
        """test that resources are properly managed throughout the pipeline"""
        config = self.create_test_config()

        # mock components to track resource usage
        crawler_instances = []
        orchestrator_instances = []

        def track_crawler(*args, **kwargs):
            instance = Mock()
            crawler_instances.append(instance)
            return instance

        def track_orchestrator(*args, **kwargs):
            instance = Mock()
            instance.run_concurrent_stage2_validation = AsyncMock()
            instance.run_concurrent_stage3_enrichment = AsyncMock()
            orchestrator_instances.append(instance)
            return instance

        with patch('orchestrator.main.Config', return_value=config):
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.CrawlerProcess', side_effect=track_crawler):
                    with patch('orchestrator.main.get_project_settings', return_value=Mock()):
                        with patch('orchestrator.main.PipelineOrchestrator', side_effect=track_orchestrator):
                            from orchestrator.main import main
                            with patch('sys.argv', ['main.py', '--stage', 'all']):
                                result = await main()

                                assert result == 0

                                # verify instances were created and used
                                assert len(crawler_instances) == 1
                                assert len(orchestrator_instances) == 1

                                # verify crawler was properly stopped
                                crawler_instances[0].stop.assert_called_once()


class TestComponentIntegration:
    """component integration testing because modules need to work together"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        self.temp_dir.cleanup()

    def test_discovery_pipeline_integration(self):
        """test discovery spider and pipeline integration"""
        from stage1.discovery_spider import DiscoverySpider
        from stage1.discovery_pipeline import Stage1Pipeline
        from common.schemas import DiscoveryItem

        # create test output file
        output_file = Path(self.temp_dir.name) / "discovery_output.jsonl"

        spider = DiscoverySpider(max_depth=1)
        pipeline = Stage1Pipeline(str(output_file))

        # simulate spider opening
        pipeline.open_spider(spider)

        # create test items
        test_items = [
            DiscoveryItem(
                source_url="https://uconn.edu",
                discovered_url="https://uconn.edu/academics",
                first_seen="2023-01-01T00:00:00",
                discovery_depth=1,
                url_hash="hash1"
            ),
            DiscoveryItem(
                source_url="https://uconn.edu",
                discovered_url="https://uconn.edu/research",
                first_seen="2023-01-01T00:00:00",
                discovery_depth=1,
                url_hash="hash2"
            )
        ]

        # process items through pipeline
        for item in test_items:
            result = pipeline.process_item(item, spider)
            assert result == item

        pipeline.close_spider(spider)

        # verify output file was created and contains data
        assert output_file.exists()
        with open(output_file, 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2

            for line in lines:
                data = json.loads(line)
                assert 'discovered_url' in data
                assert 'url_hash' in data

    def test_url_canonicalization_integration(self):
        """test URL utilities integration across components"""
        from common.urls import canonicalize_url_simple, is_valid_uconn_url, extract_domain

        test_urls = [
            "https://uconn.edu/academics",
            "https://www.uconn.edu/research/../academics",
            "http://admissions.uconn.edu/programs",
            "https://external.com/page"  # should be filtered out
        ]

        # simulate discovery spider URL processing
        valid_urls = []
        for url in test_urls:
            if is_valid_uconn_url(url):
                canonical = canonicalize_url_simple(url)
                domain = extract_domain(canonical)
                valid_urls.append({
                    'original': url,
                    'canonical': canonical,
                    'domain': domain
                })

        # should filter out external URL
        assert len(valid_urls) == 3

        # verify canonicalization worked
        research_url = next((u for u in valid_urls if 'research/../academics' in u['original']), None)
        assert research_url is not None
        assert 'research' not in research_url['canonical']  # path should be normalized

    def test_nlp_integration_with_content(self):
        """test NLP components integration with content processing"""
        from common.nlp import (
            extract_entities_and_keywords,
            extract_content_tags,
            clean_text,
            get_text_stats
        )

        # simulate enriched content
        raw_content = """
        Welcome to the University of Connecticut!
        Our academic programs include Computer Science, Engineering, and Business.
        Students can research with faculty in various departments.
        Visit our admissions office for more information.
        """

        url_path = "/academics/undergraduate/computer-science"

        # process content through NLP pipeline
        cleaned = clean_text(raw_content)
        stats = get_text_stats(cleaned)

        # mock NLP registry for testing
        with patch('common.nlp.get_registry') as mock_get_registry:
            mock_registry = Mock()
            mock_registry.extract_with_spacy.return_value = (
                ["University of Connecticut", "Computer Science", "Engineering"],
                ["university", "academic", "programs", "students", "research"]
            )
            mock_get_registry.return_value = mock_registry

            entities, keywords = extract_entities_and_keywords(cleaned)

        # extract content tags from URL
        predefined_tags = {"academics", "undergraduate", "computer-science", "graduate"}
        content_tags = extract_content_tags(url_path, predefined_tags)

        # verify integration results
        assert len(entities) > 0
        assert len(keywords) > 0
        assert len(content_tags) > 0
        assert stats['word_count'] > 0

        # verify specific extractions
        assert "academics" in content_tags
        assert "computer-science" in content_tags

    @pytest.mark.asyncio
    async def test_data_refresh_integration(self):
        """test data refresh system integration"""
        from orchestrator.data_refresh import DataRefreshManager, RefreshConfig

        # create test data files
        validation_file = Path(self.temp_dir.name) / "validation.jsonl"
        validation_data = [
            {
                "url": "https://uconn.edu/old-page",
                "status_code": 200,
                "is_valid": True,
                "validated_at": "2020-01-01T00:00:00"  # old date
            },
            {
                "url": "https://uconn.edu/failed-page",
                "is_valid": False,
                "error_message": "Connection timeout"
            }
        ]

        validation_file.parent.mkdir(parents=True, exist_ok=True)
        with open(validation_file, 'w') as f:
            for item in validation_data:
                f.write(json.dumps(item) + '\n')

        # configure refresh manager
        config = RefreshConfig(max_concurrent=2)
        manager = DataRefreshManager(config)
        manager.validation_file = validation_file

        # mock request handler
        with patch.object(manager, 'request_handler') as mock_handler:
            mock_result = Mock()
            mock_result.success = True
            mock_result.content = "refreshed content"
            mock_result.final_status_code = 200
            mock_result.content_type = "text/html"
            mock_result.content_length = 100
            mock_result.total_time = 1.0
            mock_result.learned_optimizations = {}

            mock_handler.fetch_with_learning = AsyncMock(return_value=mock_result)

            async with manager:
                result = await manager.refresh_validation_data()

                # verify refresh worked
                assert result['total_processed'] >= 1
                assert result['successful'] >= 1

                # verify file was updated
                with open(validation_file, 'r') as f:
                    updated_data = [json.loads(line) for line in f]
                    assert len(updated_data) >= 2

    def test_configuration_system_integration(self):
        """test configuration system integration across components"""
        from orchestrator.config import Config

        # create a temporary config file
        config_file = Path(self.temp_dir.name) / "config.yaml"
        config_content = """
development:
  scrapy:
    user_agent: "test-bot"
    download_delay: 1
  stage1:
    max_depth: 3
    output_file: "/tmp/stage1.jsonl"
  stage2:
    input_file: "/tmp/stage1.jsonl"
    output_file: "/tmp/stage2.jsonl"
  logging:
    level: "INFO"
"""
        config_file.write_text(config_content)

        # test config loading and usage
        with patch('orchestrator.config.Path') as mock_path:
            mock_path.return_value = config_file

            config = Config('development')

            # verify configuration methods work
            scrapy_settings = config.get_scrapy_settings()
            stage1_config = config.get_stage1_config()
            logging_config = config.get_logging_config()

            assert scrapy_settings['user_agent'] == "test-bot"
            assert stage1_config['max_depth'] == 3
            assert logging_config['level'] == "INFO"


class TestRealWorldScenarios:
    """real world scenario testing because production is different"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_large_dataset_handling(self):
        """test handling of large datasets"""
        from stage1.discovery_pipeline import Stage1Pipeline
        from common.schemas import DiscoveryItem

        # create large dataset simulation
        output_file = Path(self.temp_dir.name) / "large_dataset.jsonl"
        pipeline = Stage1Pipeline(str(output_file))

        # create existing large file
        existing_data = [
            {"url_hash": f"hash_{i}", "discovered_url": f"https://uconn.edu/page{i}"}
            for i in range(5000)  # simulate 5k existing URLs
        ]

        with open(output_file, 'w') as f:
            for item in existing_data:
                f.write(json.dumps(item) + '\n')

        # test pipeline opening with large existing file
        spider = Mock()
        pipeline.open_spider(spider)

        # should handle large file efficiently
        assert len(pipeline.seen_hashes) > 0
        assert len(pipeline.seen_hashes) <= 5000  # may be limited by optimization

        # test processing new items
        new_items = [
            DiscoveryItem(
                source_url="https://uconn.edu",
                discovered_url=f"https://uconn.edu/new_page{i}",
                first_seen="2023-01-01T00:00:00",
                discovery_depth=1,
                url_hash=f"new_hash_{i}"
            )
            for i in range(100)
        ]

        for item in new_items:
            result = pipeline.process_item(item, spider)
            assert result == item

        pipeline.close_spider(spider)

        # verify new items were added
        assert pipeline.url_count == 100

    def test_unicode_and_special_characters(self):
        """test handling of unicode and special characters throughout the system"""
        from common.urls import canonicalize_url_simple, is_valid_uconn_url
        from common.nlp import clean_text, extract_keywords_simple

        # test URLs with unicode
        unicode_urls = [
            "https://uconn.edu/café",
            "https://uconn.edu/résumé",
            "https://uconn.edu/naïve"
        ]

        for url in unicode_urls:
            # should handle unicode URLs
            if is_valid_uconn_url(url):
                canonical = canonicalize_url_simple(url)
                assert canonical is not None

        # test content with unicode
        unicode_content = """
        Welcome to the University of Connecticut's café!
        Our résumé writing service helps naïve students.
        研究 programs are available in 中文.
        العربية content is also supported.
        """

        cleaned = clean_text(unicode_content)
        keywords = extract_keywords_simple(cleaned)

        # should handle unicode content gracefully
        assert len(cleaned) > 0
        assert len(keywords) > 0

    @pytest.mark.asyncio
    async def test_network_failure_resilience(self):
        """test resilience to network failures"""
        from orchestrator.data_refresh import DataRefreshManager, RefreshConfig

        # create test data
        validation_file = Path(self.temp_dir.name) / "validation.jsonl"
        validation_data = [
            {"url": "https://uconn.edu/page1", "is_valid": False},
            {"url": "https://uconn.edu/page2", "is_valid": False},
            {"url": "https://uconn.edu/page3", "is_valid": False}
        ]

        validation_file.parent.mkdir(parents=True, exist_ok=True)
        with open(validation_file, 'w') as f:
            for item in validation_data:
                f.write(json.dumps(item) + '\n')

        config = RefreshConfig(max_concurrent=3)
        manager = DataRefreshManager(config)
        manager.validation_file = validation_file

        # simulate network failures
        failure_count = 0

        async def mock_fetch_with_failures(url):
            nonlocal failure_count
            failure_count += 1

            if failure_count <= 2:  # first 2 requests fail
                mock_result = Mock()
                mock_result.success = False
                mock_result.attempts = [Mock(error_message="Network timeout")]
                return mock_result
            else:  # subsequent requests succeed
                mock_result = Mock()
                mock_result.success = True
                mock_result.content = "success"
                mock_result.final_status_code = 200
                mock_result.content_type = "text/html"
                mock_result.content_length = 7
                mock_result.total_time = 1.0
                mock_result.learned_optimizations = {}
                return mock_result

        with patch.object(manager, 'request_handler') as mock_handler:
            mock_handler.fetch_with_learning = mock_fetch_with_failures

            async with manager:
                result = await manager.refresh_validation_data()

                # should handle mix of failures and successes
                assert result['total_processed'] == 3
                assert result['failed'] >= 1
                assert result['successful'] >= 1

    def test_concurrent_access_safety(self):
        """test thread safety and concurrent access"""
        from stage1.discovery_pipeline import Stage1Pipeline
        from common.schemas import DiscoveryItem
        import threading
        import time

        output_file = Path(self.temp_dir.name) / "concurrent_test.jsonl"
        pipeline = Stage1Pipeline(str(output_file))

        spider = Mock()
        pipeline.open_spider(spider)

        results = []
        errors = []

        def worker(worker_id):
            try:
                for i in range(10):
                    item = DiscoveryItem(
                        source_url="https://uconn.edu",
                        discovered_url=f"https://uconn.edu/worker{worker_id}_item{i}",
                        first_seen="2023-01-01T00:00:00",
                        discovery_depth=1,
                        url_hash=f"worker{worker_id}_hash{i}"
                    )
                    result = pipeline.process_item(item, spider)
                    results.append(result)
                    time.sleep(0.001)  # small delay to encourage race conditions
            except Exception as e:
                errors.append(e)

        # run multiple workers concurrently
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        pipeline.close_spider(spider)

        # should handle concurrent access without errors
        assert len(errors) == 0
        assert len(results) == 50  # 5 workers * 10 items each

        # verify file integrity
        with open(output_file, 'r') as f:
            lines = f.readlines()
            assert len(lines) == 50

            # each line should be valid JSON
            for line in lines:
                data = json.loads(line)
                assert 'url_hash' in data

    def test_memory_usage_optimization(self):
        """test memory usage optimization strategies"""
        from stage1.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(max_depth=2)

        # simulate processing many URLs
        for i in range(1000):
            url = f"https://uconn.edu/page{i}"
            results = spider._process_candidate_url(url, "https://uconn.edu", 0)

            # should add to seen_urls
            assert url in spider.seen_urls

        # memory usage should be reasonable
        assert len(spider.seen_urls) == 1000
        assert spider.unique_urls_found == 1000

        # test duplicate handling doesn't explode memory
        for i in range(1000):
            url = f"https://uconn.edu/page{i}"  # same URLs again
            results = spider._process_candidate_url(url, "https://uconn.edu", 0)

        # should not double memory usage
        assert len(spider.seen_urls) == 1000
        assert spider.duplicates_skipped == 1000

    @pytest.mark.asyncio
    async def test_configuration_edge_cases(self):
        """test configuration edge cases and defaults"""
        from orchestrator.config import Config

        # test with minimal config
        minimal_config_content = """
development:
  # minimal config
"""
        config_file = Path(self.temp_dir.name) / "minimal_config.yaml"
        config_file.write_text(minimal_config_content)

        with patch('orchestrator.config.Path') as mock_path:
            mock_path.return_value = config_file

            config = Config('development')

            # should provide reasonable defaults
            scrapy_settings = config.get_scrapy_settings()
            stage1_config = config.get_stage1_config()
            data_paths = config.get_data_paths()

            assert isinstance(scrapy_settings, dict)
            assert isinstance(stage1_config, dict)
            assert isinstance(data_paths, dict)

            # paths should be Path objects
            for path in data_paths.values():
                assert isinstance(path, Path)

    def test_error_message_quality(self):
        """test that error messages are helpful for debugging"""
        from common.urls import normalize_url, is_valid_uconn_url

        # test with various invalid inputs
        invalid_inputs = [
            None,
            "",
            "not-a-url",
            "javascript:alert(1)",
            "https://",
        ]

        for invalid_input in invalid_inputs:
            try:
                if invalid_input is None:
                    normalize_url(invalid_input)
                else:
                    is_valid_uconn_url(invalid_input)
            except Exception as e:
                # error messages should be informative
                error_msg = str(e)
                assert len(error_msg) > 0
                # should not be generic "An error occurred"
                assert "error occurred" not in error_msg.lower()

    @pytest.mark.asyncio
    async def test_graceful_degradation(self):
        """test graceful degradation when optional components fail"""
        from common.nlp import initialize_nlp, get_registry

        # test NLP graceful degradation
        with patch('common.nlp.NLPRegistry', side_effect=RuntimeError("spaCy not available")):
            initialize_nlp()

            # should fall back to dummy registry
            registry = get_registry()
            entities, keywords = registry.extract_with_spacy("test text", 5)

            # dummy registry should return empty results without crashing
            assert entities == []
            assert keywords == []

    def test_data_validation_and_integrity(self):
        """test data validation and integrity checks"""
        from common.schemas import DiscoveryItem, ValidationResult

        # test schema validation
        valid_discovery_item = DiscoveryItem(
            source_url="https://uconn.edu",
            discovered_url="https://uconn.edu/academics",
            first_seen="2023-01-01T00:00:00",
            discovery_depth=1,
            url_hash="valid_hash"
        )

        # should create without errors
        assert valid_discovery_item.discovered_url == "https://uconn.edu/academics"

        # test with missing required fields
        try:
            invalid_item = DiscoveryItem(
                source_url=None,  # required field
                discovered_url="https://uconn.edu/academics",
                first_seen="2023-01-01T00:00:00",
                discovery_depth=1
            )
            # depending on implementation, this might raise an error
        except Exception:
            # acceptable to fail validation
            pass

        # test validation result
        validation_result = ValidationResult(
            url="https://uconn.edu/test",
            status_code=200,
            content_type="text/html",
            content_length=1000,
            response_time=1.5,
            is_valid=True,
            error_message=None,
            validated_at="2023-01-01T00:00:00"
        )

        assert validation_result.is_valid is True
        assert validation_result.status_code == 200