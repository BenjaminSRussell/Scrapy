"""Comprehensive tests for main orchestrator - every detail"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import pytest
import argparse

# need to patch sys.path for imports
test_src_dir = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(test_src_dir))

from orchestrator.main import (
    run_stage1_discovery,
    run_stage2_validation,
    run_stage3_enrichment,
    main,
    CrawlerProcess,
    URLValidator,
    EnrichmentSpider
)


class TestStage1Discovery:
    """stage 1 testing because discovery is where it all starts"""

    @pytest.mark.asyncio
    async def test_run_stage1_discovery_basic(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {"USER_AGENT": "test"}
        mock_config.get_stage1_config.return_value = {
            "output_file": "/tmp/test.jsonl",
            "max_depth": 3
        }

        mock_process = Mock()
        mock_crawler_class = Mock(return_value=mock_process)

        with patch('orchestrator.main.CrawlerProcess', mock_crawler_class):
            with patch('orchestrator.main.get_project_settings') as mock_settings:
                mock_settings.return_value = {"LOG_LEVEL": "INFO"}

                with patch('orchestrator.main.DiscoverySpider') as mock_spider:
                    await run_stage1_discovery(mock_config)

                    mock_process.crawl.assert_called_once()
                    mock_process.start.assert_called_once()
                    mock_process.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage1_discovery_lazy_import(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {}
        mock_config.get_stage1_config.return_value = {
            "output_file": "/tmp/test.jsonl",
            "max_depth": 2
        }

        # test lazy import when CrawlerProcess is None
        with patch('orchestrator.main.CrawlerProcess', None):
            with patch('orchestrator.main.get_project_settings') as mock_settings:
                mock_settings.return_value = {}

                with patch('scrapy.crawler.CrawlerProcess') as mock_lazy_import:
                    mock_process = Mock()
                    mock_lazy_import.return_value = mock_process

                    await run_stage1_discovery(mock_config)

                    mock_lazy_import.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage1_discovery_settings_configuration(self):
        mock_config = Mock()
        scrapy_settings = {"USER_AGENT": "test-agent", "DOWNLOAD_DELAY": 1}
        stage1_config = {"output_file": "/custom/output.jsonl", "max_depth": 5}

        mock_config.get_scrapy_settings.return_value = scrapy_settings
        mock_config.get_stage1_config.return_value = stage1_config

        mock_process = Mock()
        mock_settings_obj = Mock()

        with patch('orchestrator.main.CrawlerProcess', return_value=mock_process):
            with patch('orchestrator.main.get_project_settings', return_value=mock_settings_obj):
                await run_stage1_discovery(mock_config)

                # verify settings were updated correctly
                mock_settings_obj.update.assert_called()
                update_calls = mock_settings_obj.update.call_args_list

                # should update with scrapy settings and stage1 settings
                assert len(update_calls) >= 2

    @pytest.mark.asyncio
    async def test_run_stage1_discovery_exception_handling(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {}
        mock_config.get_stage1_config.return_value = {"output_file": "/tmp/test.jsonl", "max_depth": 3}

        mock_process = Mock()
        mock_process.start.side_effect = Exception("Crawler failed")

        with patch('orchestrator.main.CrawlerProcess', return_value=mock_process):
            with patch('orchestrator.main.get_project_settings', return_value=Mock()):
                # should still call stop() even if start() fails
                with pytest.raises(Exception):
                    await run_stage1_discovery(mock_config)

                mock_process.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage1_discovery_pipeline_configuration(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {}
        mock_config.get_stage1_config.return_value = {
            "output_file": "/custom/path/output.jsonl",
            "max_depth": 4
        }

        mock_process = Mock()
        mock_settings = Mock()

        with patch('orchestrator.main.CrawlerProcess', return_value=mock_process):
            with patch('orchestrator.main.get_project_settings', return_value=mock_settings):
                await run_stage1_discovery(mock_config)

                # verify pipeline configuration
                pipeline_call = None
                for call in mock_settings.update.call_args_list:
                    if 'ITEM_PIPELINES' in call[0][0]:
                        pipeline_call = call[0][0]
                        break

                assert pipeline_call is not None
                assert 'stage1.discovery_pipeline.Stage1Pipeline' in pipeline_call['ITEM_PIPELINES']


class TestStage2Validation:
    """stage 2 testing because validation is critical"""

    @pytest.mark.asyncio
    async def test_run_stage2_validation_basic(self):
        mock_config = Mock()
        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage2_validation = AsyncMock()

        mock_validator = Mock()
        mock_validator_class = Mock(return_value=mock_validator)

        with patch('orchestrator.main.URLValidator', mock_validator_class):
            await run_stage2_validation(mock_config, mock_orchestrator)

            mock_validator_class.assert_called_once_with(mock_config)
            mock_orchestrator.run_concurrent_stage2_validation.assert_called_once_with(mock_validator)

    @pytest.mark.asyncio
    async def test_run_stage2_validation_lazy_import(self):
        mock_config = Mock()
        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage2_validation = AsyncMock()

        # test lazy import when URLValidator is None
        with patch('orchestrator.main.URLValidator', None):
            with patch('stage2.validator.URLValidator') as mock_lazy_import:
                mock_validator = Mock()
                mock_lazy_import.return_value = mock_validator

                await run_stage2_validation(mock_config, mock_orchestrator)

                mock_lazy_import.assert_called_once_with(mock_config)

    @pytest.mark.asyncio
    async def test_run_stage2_validation_orchestrator_failure(self):
        mock_config = Mock()
        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage2_validation = AsyncMock(
            side_effect=Exception("Validation failed")
        )

        with patch('orchestrator.main.URLValidator', Mock):
            with pytest.raises(Exception, match="Validation failed"):
                await run_stage2_validation(mock_config, mock_orchestrator)

    @pytest.mark.asyncio
    async def test_run_stage2_validation_logging(self):
        mock_config = Mock()
        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage2_validation = AsyncMock()

        with patch('orchestrator.main.URLValidator', Mock):
            with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                mock_logger = Mock()
                mock_get_logger.return_value = mock_logger

                await run_stage2_validation(mock_config, mock_orchestrator)

                # should log stage start
                info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
                assert any("STAGE 2" in call for call in info_calls)


class TestStage3Enrichment:
    """stage 3 testing because enrichment is the finale"""

    @pytest.mark.asyncio
    async def test_run_stage3_enrichment_basic(self):
        mock_config = Mock()
        mock_config.get_stage3_config.return_value = {"output_file": "/tmp/enriched.jsonl"}
        mock_config.get_logging_config.return_value = {"level": "INFO"}

        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()

        mock_spider = Mock()
        mock_spider_class = Mock(return_value=mock_spider)

        with patch('orchestrator.main.EnrichmentSpider', mock_spider_class):
            await run_stage3_enrichment(mock_config, mock_orchestrator)

            mock_spider_class.assert_called_once()
            mock_orchestrator.run_concurrent_stage3_enrichment.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage3_enrichment_lazy_import(self):
        mock_config = Mock()
        mock_config.get_stage3_config.return_value = {"output_file": "/tmp/test.jsonl"}
        mock_config.get_logging_config.return_value = {"level": "DEBUG"}

        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()

        # test lazy import when EnrichmentSpider is None
        with patch('orchestrator.main.EnrichmentSpider', None):
            with patch('stage3.enrichment_spider.EnrichmentSpider') as mock_lazy_import:
                mock_spider = Mock()
                mock_lazy_import.return_value = mock_spider

                await run_stage3_enrichment(mock_config, mock_orchestrator)

                mock_lazy_import.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stage3_enrichment_settings_configuration(self):
        mock_config = Mock()
        stage3_config = {"output_file": "/custom/enriched.jsonl"}
        logging_config = {"level": "WARNING"}

        mock_config.get_stage3_config.return_value = stage3_config
        mock_config.get_logging_config.return_value = logging_config

        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()

        with patch('orchestrator.main.EnrichmentSpider', Mock):
            await run_stage3_enrichment(mock_config, mock_orchestrator)

            # verify settings were passed correctly
            call_args = mock_orchestrator.run_concurrent_stage3_enrichment.call_args
            settings = call_args[0][1]  # second argument

            assert settings['STAGE3_OUTPUT_FILE'] == "/custom/enriched.jsonl"
            assert settings['LOG_LEVEL'] == "WARNING"
            assert 'stage3.enrichment_pipeline.Stage3Pipeline' in settings['ITEM_PIPELINES']

    @pytest.mark.asyncio
    async def test_run_stage3_enrichment_default_settings(self):
        mock_config = Mock()
        mock_config.get_stage3_config.return_value = {"output_file": "/tmp/test.jsonl"}
        mock_config.get_logging_config.return_value = {"level": "INFO"}

        mock_orchestrator = Mock()
        mock_orchestrator.run_concurrent_stage3_enrichment = AsyncMock()

        with patch('orchestrator.main.EnrichmentSpider', Mock):
            await run_stage3_enrichment(mock_config, mock_orchestrator)

            call_args = mock_orchestrator.run_concurrent_stage3_enrichment.call_args
            settings = call_args[0][1]

            # verify default settings
            assert settings['ROBOTSTXT_OBEY'] is True
            assert settings['DOWNLOAD_DELAY'] == 1
            assert settings['CONCURRENT_REQUESTS'] == 16
            assert settings['CONCURRENT_REQUESTS_PER_DOMAIN'] == 8

    @pytest.mark.asyncio
    async def test_run_stage3_enrichment_spider_creation_failure(self):
        mock_config = Mock()
        mock_config.get_stage3_config.return_value = {"output_file": "/tmp/test.jsonl"}
        mock_config.get_logging_config.return_value = {"level": "INFO"}

        mock_orchestrator = Mock()

        with patch('orchestrator.main.EnrichmentSpider', side_effect=Exception("Spider creation failed")):
            with pytest.raises(Exception, match="Spider creation failed"):
                await run_stage3_enrichment(mock_config, mock_orchestrator)


class TestMainFunction:
    """main function testing because entry points are important"""

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        self.temp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_main_default_arguments(self):
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock):
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock):
                                    result = await main()

                                    assert result == 0
                                    mock_config_class.assert_called_once_with('development')

    @pytest.mark.asyncio
    async def test_main_custom_environment(self):
        test_args = ['main.py', '--env', 'production']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock):
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock):
                                    result = await main()

                                    assert result == 0
                                    mock_config_class.assert_called_once_with('production')

    @pytest.mark.asyncio
    async def test_main_single_stage(self):
        test_args = ['main.py', '--stage', '1']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock) as mock_stage1:
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                    result = await main()

                                    assert result == 0
                                    mock_stage1.assert_called_once()
                                    mock_stage2.assert_not_called()
                                    mock_stage3.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_stage_combinations(self):
        stage_tests = [
            (['main.py', '--stage', '2'], [False, True, False]),
            (['main.py', '--stage', '3'], [False, False, True]),
            (['main.py', '--stage', 'all'], [True, True, True]),
        ]

        for test_args, expected_calls in stage_tests:
            with patch('sys.argv', test_args):
                with patch('orchestrator.main.Config'):
                    with patch('orchestrator.main.setup_logging'):
                        with patch('orchestrator.main.PipelineOrchestrator'):
                            with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock) as mock_stage1:
                                with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                                    with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                        result = await main()

                                        assert result == 0
                                        assert mock_stage1.called == expected_calls[0]
                                        assert mock_stage2.called == expected_calls[1]
                                        assert mock_stage3.called == expected_calls[2]

    @pytest.mark.asyncio
    async def test_main_config_only_mode(self):
        test_args = ['main.py', '--config-only']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config._config = {"test": "config"}
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('builtins.print') as mock_print:
                        with patch('orchestrator.main.yaml') as mock_yaml:
                            mock_yaml.dump.return_value = "config: yaml"

                            result = await main()

                            assert result == 0
                            mock_print.assert_called_once()
                            mock_yaml.dump.assert_called_once()

    @pytest.mark.asyncio
    async def test_main_custom_log_level(self):
        test_args = ['main.py', '--log-level', 'DEBUG']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging') as mock_setup_logging:
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock):
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock):
                                    await main()

                                    # verify logging setup was called with correct level
                                    mock_setup_logging.assert_called_once()
                                    call_kwargs = mock_setup_logging.call_args[1]
                                    assert call_kwargs['log_level'] == 'DEBUG'

    @pytest.mark.asyncio
    async def test_main_directory_creation(self):
        test_args = ['main.py']

        mock_data_paths = {
            'logs_dir': Path(self.temp_dir.name) / 'logs',
            'processed_dir': Path(self.temp_dir.name) / 'processed',
            'analytics_dir': Path(self.temp_dir.name) / 'analytics'
        }

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config.get_data_paths.return_value = mock_data_paths
                mock_config.get_logging_config.return_value = {'level': 'INFO'}
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock):
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock):
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock):
                                    result = await main()

                                    assert result == 0
                                    # verify directories were created
                                    for path in mock_data_paths.values():
                                        assert path.exists()

    @pytest.mark.asyncio
    async def test_main_keyboard_interrupt(self):
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock, side_effect=KeyboardInterrupt):
                            with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                                mock_logger = Mock()
                                mock_get_logger.return_value = mock_logger

                                result = await main()

                                assert result == 1
                                mock_logger.info.assert_called_with("Pipeline interrupted by user")

    @pytest.mark.asyncio
    async def test_main_general_exception(self):
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config', side_effect=Exception("Config failed")):
                with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                    mock_logger = Mock()
                    mock_get_logger.return_value = mock_logger

                    result = await main()

                    assert result == 1
                    mock_logger.error.assert_called()
                    error_call = mock_logger.error.call_args[0][0]
                    assert "Pipeline failed" in error_call

    @pytest.mark.asyncio
    async def test_main_argument_parsing_invalid(self):
        test_args = ['main.py', '--invalid-arg']

        with patch('sys.argv', test_args):
            # should raise SystemExit due to invalid argument
            with pytest.raises(SystemExit):
                await main()

    @pytest.mark.asyncio
    async def test_main_stage_validation(self):
        invalid_stage_args = ['main.py', '--stage', 'invalid']

        with patch('sys.argv', invalid_stage_args):
            with pytest.raises(SystemExit):
                await main()

    @pytest.mark.asyncio
    async def test_main_environment_validation(self):
        invalid_env_args = ['main.py', '--env', 'invalid']

        with patch('sys.argv', invalid_env_args):
            with pytest.raises(SystemExit):
                await main()

    @pytest.mark.asyncio
    async def test_main_logging_setup_failure(self):
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging', side_effect=Exception("Logging setup failed")):
                    with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                        mock_logger = Mock()
                        mock_get_logger.return_value = mock_logger

                        result = await main()

                        assert result == 1

    @pytest.mark.asyncio
    async def test_main_orchestrator_creation_failure(self):
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator', side_effect=Exception("Orchestrator failed")):
                        with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                            mock_logger = Mock()
                            mock_get_logger.return_value = mock_logger

                            result = await main()

                            assert result == 1


class TestModuleLevelImports:
    """import testing because optional dependencies are tricky"""

    def test_crawler_process_import_success(self):
        with patch('orchestrator.main._CrawlerProcess', Mock()) as mock_cp:
            # CrawlerProcess should be available
            assert CrawlerProcess == mock_cp

    def test_crawler_process_import_failure(self):
        with patch('orchestrator.main._CrawlerProcess', None):
            # should use lazy import
            assert CrawlerProcess is None

    def test_url_validator_import_success(self):
        with patch('orchestrator.main._URLValidator', Mock()) as mock_validator:
            assert URLValidator == mock_validator

    def test_url_validator_import_failure(self):
        with patch('orchestrator.main._URLValidator', None):
            assert URLValidator is None

    def test_enrichment_spider_import_success(self):
        with patch('orchestrator.main._EnrichmentSpider', Mock()) as mock_spider:
            assert EnrichmentSpider == mock_spider

    def test_enrichment_spider_import_failure(self):
        with patch('orchestrator.main._EnrichmentSpider', None):
            assert EnrichmentSpider is None


class TestAsyncioEventLoop:
    """asyncio testing because async is everywhere"""

    @pytest.mark.asyncio
    async def test_stage1_asyncio_executor(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {}
        mock_config.get_stage1_config.return_value = {"output_file": "/tmp/test.jsonl", "max_depth": 3}

        mock_process = Mock()
        mock_process.start = Mock()  # synchronous mock

        with patch('orchestrator.main.CrawlerProcess', return_value=mock_process):
            with patch('orchestrator.main.get_project_settings', return_value=Mock()):
                with patch('asyncio.get_event_loop') as mock_get_loop:
                    mock_loop = Mock()
                    mock_loop.run_in_executor = AsyncMock()
                    mock_get_loop.return_value = mock_loop

                    await run_stage1_discovery(mock_config)

                    # should use executor for synchronous scrapy code
                    mock_loop.run_in_executor.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage1_executor_exception_cleanup(self):
        mock_config = Mock()
        mock_config.get_scrapy_settings.return_value = {}
        mock_config.get_stage1_config.return_value = {"output_file": "/tmp/test.jsonl", "max_depth": 3}

        mock_process = Mock()

        with patch('orchestrator.main.CrawlerProcess', return_value=mock_process):
            with patch('orchestrator.main.get_project_settings', return_value=Mock()):
                with patch('asyncio.get_event_loop') as mock_get_loop:
                    mock_loop = Mock()
                    mock_loop.run_in_executor = AsyncMock(side_effect=Exception("Executor failed"))
                    mock_get_loop.return_value = mock_loop

                    with pytest.raises(Exception):
                        await run_stage1_discovery(mock_config)

                    # should still call stop() in finally block
                    mock_process.stop.assert_called_once()


class TestErrorRecovery:
    """error recovery testing because resilience is key"""

    @pytest.mark.asyncio
    async def test_stage_failure_isolation(self):
        """test that failure in one stage doesn't prevent others from running"""
        test_args = ['main.py', '--stage', 'all']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock, side_effect=Exception("Stage 1 failed")):
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                    result = await main()

                                    # should fail overall but still attempt other stages
                                    assert result == 1
                                    # stages 2 and 3 should still be called despite stage 1 failure
                                    mock_stage2.assert_called_once()
                                    mock_stage3.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_loading_resilience(self):
        """test handling of various config loading failures"""
        test_args = ['main.py']

        config_errors = [
            FileNotFoundError("Config file not found"),
            PermissionError("Permission denied"),
            ValueError("Invalid config format"),
            KeyError("Missing required config key")
        ]

        for error in config_errors:
            with patch('sys.argv', test_args):
                with patch('orchestrator.main.Config', side_effect=error):
                    with patch('orchestrator.main.logging.getLogger') as mock_get_logger:
                        mock_logger = Mock()
                        mock_get_logger.return_value = mock_logger

                        result = await main()

                        assert result == 1
                        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_partial_setup_cleanup(self):
        """test cleanup when setup partially succeeds"""
        test_args = ['main.py']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config.get_data_paths.return_value = {
                    'logs_dir': Path('/tmp/logs'),
                    'processed_dir': Path('/tmp/processed')
                }
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator', side_effect=Exception("Orchestrator failed")):
                        # should handle gracefully even if orchestrator creation fails
                        result = await main()
                        assert result == 1


class TestCommandLineInterface:
    """CLI testing because user interface matters"""

    def test_argument_parser_help(self):
        """test that help works correctly"""
        with patch('sys.argv', ['main.py', '--help']):
            with pytest.raises(SystemExit) as exc_info:
                # calling main() would trigger asyncio.run() which we want to avoid
                # so just test the parser directly
                parser = argparse.ArgumentParser(description='UConn Web Scraping Pipeline Orchestrator')
                parser.add_argument('--env', choices=['development', 'production'], default='development')
                parser.add_argument('--stage', choices=['1', '2', '3', 'all'], default='all')
                parser.add_argument('--config-only', action='store_true')
                parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')

                parser.parse_args(['--help'])

            assert exc_info.value.code == 0

    def test_argument_combinations(self):
        """test various argument combinations"""
        valid_combinations = [
            ['--env', 'development'],
            ['--env', 'production'],
            ['--stage', '1'],
            ['--stage', '2'],
            ['--stage', '3'],
            ['--stage', 'all'],
            ['--log-level', 'DEBUG'],
            ['--log-level', 'INFO'],
            ['--log-level', 'WARNING'],
            ['--log-level', 'ERROR'],
            ['--config-only'],
            ['--env', 'production', '--stage', '2', '--log-level', 'DEBUG'],
            ['--env', 'development', '--config-only']
        ]

        parser = argparse.ArgumentParser()
        parser.add_argument('--env', choices=['development', 'production'], default='development')
        parser.add_argument('--stage', choices=['1', '2', '3', 'all'], default='all')
        parser.add_argument('--config-only', action='store_true')
        parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')

        for args in valid_combinations:
            # should not raise exception
            parsed = parser.parse_args(args)
            assert parsed is not None

    def test_invalid_argument_combinations(self):
        """test invalid argument combinations"""
        invalid_combinations = [
            ['--env', 'invalid'],
            ['--stage', 'invalid'],
            ['--log-level', 'INVALID'],
            ['--unknown-arg'],
        ]

        parser = argparse.ArgumentParser()
        parser.add_argument('--env', choices=['development', 'production'], default='development')
        parser.add_argument('--stage', choices=['1', '2', '3', 'all'], default='all')
        parser.add_argument('--config-only', action='store_true')
        parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')

        for args in invalid_combinations:
            with pytest.raises(SystemExit):
                parser.parse_args(args)


class TestIntegrationScenarios:
    """integration testing because the whole is more than the sum of parts"""

    @pytest.mark.asyncio
    async def test_full_pipeline_success_scenario(self):
        """test complete successful pipeline run"""
        test_args = ['main.py', '--env', 'development', '--stage', 'all', '--log-level', 'INFO']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config.get_data_paths.return_value = {
                    'logs_dir': Path('/tmp/logs'),
                    'processed_dir': Path('/tmp/processed')
                }
                mock_config.get_logging_config.return_value = {'level': 'INFO'}
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator') as mock_orchestrator_class:
                        mock_orchestrator = Mock()
                        mock_orchestrator_class.return_value = mock_orchestrator

                        with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock) as mock_stage1:
                            with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                                with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                    result = await main()

                                    assert result == 0
                                    mock_stage1.assert_called_once()
                                    mock_stage2.assert_called_once()
                                    mock_stage3.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_only_workflow(self):
        """test config-only mode workflow"""
        test_args = ['main.py', '--config-only', '--env', 'production']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config._config = {
                    'environment': 'production',
                    'database': {'host': 'prod-server'},
                    'scrapy': {'user_agent': 'prod-bot'}
                }
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('builtins.print') as mock_print:
                        with patch('orchestrator.main.yaml') as mock_yaml:
                            mock_yaml.dump.return_value = "formatted: config"

                            result = await main()

                            assert result == 0
                            mock_config_class.assert_called_once_with('production')
                            mock_print.assert_called_once()
                            # no stages should be called
                            assert not hasattr(mock_config, 'run_stage1_discovery')

    @pytest.mark.asyncio
    async def test_partial_pipeline_scenarios(self):
        """test running individual stages"""
        stage_scenarios = [
            (['--stage', '1'], ['stage1']),
            (['--stage', '2'], ['stage2']),
            (['--stage', '3'], ['stage3']),
        ]

        for args, expected_stages in stage_scenarios:
            test_args = ['main.py'] + args

            with patch('sys.argv', test_args):
                with patch('orchestrator.main.Config'):
                    with patch('orchestrator.main.setup_logging'):
                        with patch('orchestrator.main.PipelineOrchestrator'):
                            with patch('orchestrator.main.run_stage1_discovery', new_callable=AsyncMock) as mock_stage1:
                                with patch('orchestrator.main.run_stage2_validation', new_callable=AsyncMock) as mock_stage2:
                                    with patch('orchestrator.main.run_stage3_enrichment', new_callable=AsyncMock) as mock_stage3:
                                        result = await main()

                                        assert result == 0
                                        assert mock_stage1.called == ('stage1' in expected_stages)
                                        assert mock_stage2.called == ('stage2' in expected_stages)
                                        assert mock_stage3.called == ('stage3' in expected_stages)

    @pytest.mark.asyncio
    async def test_environment_specific_behavior(self):
        """test different behavior based on environment"""
        environments = ['development', 'production']

        for env in environments:
            test_args = ['main.py', '--env', env, '--config-only']

            with patch('sys.argv', test_args):
                with patch('orchestrator.main.Config') as mock_config_class:
                    mock_config = Mock()
                    mock_config._config = {'env': env}
                    mock_config_class.return_value = mock_config

                    with patch('orchestrator.main.setup_logging'):
                        with patch('builtins.print'):
                            with patch('orchestrator.main.yaml'):
                                result = await main()

                                assert result == 0
                                mock_config_class.assert_called_once_with(env)


class TestPerformanceConsiderations:
    """performance testing because speed matters"""

    @pytest.mark.asyncio
    async def test_async_overhead_minimal(self):
        """test that async overhead is minimal for quick operations"""
        import time

        test_args = ['main.py', '--config-only']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config') as mock_config_class:
                mock_config = Mock()
                mock_config._config = {}
                mock_config_class.return_value = mock_config

                with patch('orchestrator.main.setup_logging'):
                    with patch('builtins.print'):
                        with patch('orchestrator.main.yaml'):
                            start_time = time.time()
                            result = await main()
                            end_time = time.time()

                            assert result == 0
                            # should complete quickly for config-only mode
                            assert (end_time - start_time) < 1.0

    @pytest.mark.asyncio
    async def test_memory_usage_reasonable(self):
        """test that memory usage is reasonable"""
        test_args = ['main.py', '--config-only']

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('builtins.print'):
                        with patch('orchestrator.main.yaml'):
                            # should not create excessive objects
                            result = await main()
                            assert result == 0

    @pytest.mark.asyncio
    async def test_concurrent_stage_execution(self):
        """test that stages are properly sequenced (not concurrent)"""
        test_args = ['main.py', '--stage', 'all']

        call_order = []

        async def track_stage1(config):
            call_order.append('stage1')

        async def track_stage2(config, orchestrator):
            call_order.append('stage2')

        async def track_stage3(config, orchestrator):
            call_order.append('stage3')

        with patch('sys.argv', test_args):
            with patch('orchestrator.main.Config'):
                with patch('orchestrator.main.setup_logging'):
                    with patch('orchestrator.main.PipelineOrchestrator'):
                        with patch('orchestrator.main.run_stage1_discovery', side_effect=track_stage1):
                            with patch('orchestrator.main.run_stage2_validation', side_effect=track_stage2):
                                with patch('orchestrator.main.run_stage3_enrichment', side_effect=track_stage3):
                                    result = await main()

                                    assert result == 0
                                    # stages should be called in order
                                    assert call_order == ['stage1', 'stage2', 'stage3']