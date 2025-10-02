"""Tests for main orchestrator module."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from orchestrator.main import run_stage1_discovery, run_stage2_validation, run_stage3_enrichment, main


class MockConfig:
    """Mock configuration for testing."""

    def __init__(self):
        self.env = 'test'

    def get_scrapy_settings(self):
        return {
            'CONCURRENT_REQUESTS': 32,
            'DOWNLOAD_DELAY': 0.1,
            'USER_AGENT': 'TestSpider/1.0'
        }

    def get_stage1_config(self):
        return {
            'max_depth': 3,
            'output_file': 'data/processed/stage01/test_urls.jsonl',
            'seed_file': 'data/raw/seeds.csv'
        }

    def get_stage2_config(self):
        return {
            'max_workers': 10,
            'timeout': 5,
            'output_file': 'data/processed/stage02/test_validated.jsonl'
        }

    def get_stage3_config(self):
        return {
            'nlp_enabled': True,
            'output_file': 'data/processed/stage03/test_enriched.jsonl',
            'allowed_domains': ['uconn.edu'],
            'content_types': {},
            'headless_browser': {},
        }
    def get_nlp_config(self):
        return {
            'spacy_model': 'en_core_web_sm',
            'use_transformers': False
        }

    def get_logging_config(self):
        return {
            'level': 'INFO',
            'file': 'logs/test.log'
        }

    def get_data_paths(self):
        return {
            'raw_dir': Path('data/raw'),
            'processed_dir': Path('data/processed'),
            'logs_dir': Path('logs'),
            'temp_dir': Path('data/temp')
        }

@pytest.mark.asyncio
async def test_run_stage1_discovery():
    """Test Stage 1 discovery execution."""
    config = MockConfig()

    with patch('orchestrator.main.CrawlerProcess') as mock_process_class:
        mock_process = Mock()
        mock_process_class.return_value = mock_process
        mock_process.start = Mock()

        # Mock the executor to avoid blocking
        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)

            await run_stage1_discovery(config)

            # Verify CrawlerProcess was configured correctly
            mock_process_class.assert_called_once()
            settings = mock_process_class.call_args[0][0]
            assert settings['CONCURRENT_REQUESTS'] == 32
            assert settings['STAGE1_OUTPUT_FILE'] == 'data/processed/stage01/test_urls.jsonl'

            # Verify spider was crawled
            mock_process.crawl.assert_called_once()
            crawl_args = mock_process.crawl.call_args
            assert crawl_args[1]['max_depth'] == 3


@pytest.mark.asyncio
async def test_run_stage2_validation():
    """Test Stage 2 validation execution."""
    config = MockConfig()
    orchestrator = Mock()
    orchestrator.run_concurrent_stage2_validation = AsyncMock()

    with patch('orchestrator.main.URLValidator') as mock_validator_class:
        mock_validator = Mock()
        mock_validator_class.return_value = mock_validator

        await run_stage2_validation(config, orchestrator)

        # Verify validator was created with config
        mock_validator_class.assert_called_once_with(config)

        # Verify concurrent validation was called
        orchestrator.run_concurrent_stage2_validation.assert_called_once_with(mock_validator)


@pytest.mark.asyncio
async def test_run_stage3_enrichment():
    """Test Stage 3 enrichment execution."""
    config = MockConfig()
    orchestrator = Mock()
    orchestrator.run_concurrent_stage3_enrichment = AsyncMock()

    with patch('orchestrator.main.EnrichmentSpider') as mock_spider_class:
        await run_stage3_enrichment(config, orchestrator)

        orchestrator.run_concurrent_stage3_enrichment.assert_called_once()
        args, kwargs = orchestrator.run_concurrent_stage3_enrichment.call_args
        assert args[0] is mock_spider_class
        scrapy_settings = args[1]
        assert 'STAGE3_OUTPUT_FILE' in scrapy_settings
        assert scrapy_settings['LOG_LEVEL'] == 'INFO'
        spider_kwargs = kwargs.get('spider_kwargs')
        assert spider_kwargs is not None
        assert spider_kwargs['allowed_domains'] == ['uconn.edu']
        assert spider_kwargs['content_types_config'] == {}
        mock_spider_class.assert_not_called()


@pytest.mark.asyncio
async def test_main_config_only():
    """Test main function with config-only option."""
    test_args = ['main.py', '--config-only']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            with patch('orchestrator.main.setup_logging') as mock_setup_logging:
                with patch('builtins.print') as mock_print:
                    mock_config = MockConfig()
                    mock_config._config = {'test': 'config'}
                    mock_config_class.return_value = mock_config

                    result = await main()

                    assert result == 0
                    mock_print.assert_called()
                    printed_output = mock_print.call_args[0][0]
                    assert 'Configuration:' in printed_output


@pytest.mark.asyncio
async def test_main_stage_selection():
    """Test main function with specific stage selection."""
    test_args = ['main.py', '--stage', '1', '--env', 'development']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.PipelineOrchestrator') as mock_orchestrator_class:
                    with patch('orchestrator.main.run_stage1_discovery') as mock_stage1:
                        with patch('orchestrator.main.run_stage2_validation') as mock_stage2:
                            with patch('orchestrator.main.run_stage3_enrichment') as mock_stage3:
                                mock_config = MockConfig()
                                mock_config_class.return_value = mock_config
                                mock_orchestrator = Mock()
                                mock_orchestrator_class.return_value = mock_orchestrator

                                mock_stage1.return_value = AsyncMock()
                                mock_stage2.return_value = AsyncMock()
                                mock_stage3.return_value = AsyncMock()

                                result = await main()

                                assert result == 0
                                mock_stage1.assert_called_once_with(mock_config)
                                mock_stage2.assert_not_called()
                                mock_stage3.assert_not_called()


@pytest.mark.asyncio
async def test_main_all_stages():
    """Test main function running all stages."""
    test_args = ['main.py', '--stage', 'all']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.PipelineOrchestrator') as mock_orchestrator_class:
                    with patch('orchestrator.main.run_stage1_discovery') as mock_stage1:
                        with patch('orchestrator.main.run_stage2_validation') as mock_stage2:
                            with patch('orchestrator.main.run_stage3_enrichment') as mock_stage3:
                                mock_config = MockConfig()
                                mock_config_class.return_value = mock_config
                                mock_orchestrator = Mock()
                                mock_orchestrator_class.return_value = mock_orchestrator

                                mock_stage1.return_value = AsyncMock()
                                mock_stage2.return_value = AsyncMock()
                                mock_stage3.return_value = AsyncMock()

                                result = await main()

                                assert result == 0
                                mock_stage1.assert_called_once_with(mock_config)
                                mock_stage2.assert_called_once_with(mock_config, mock_orchestrator)
                                mock_stage3.assert_called_once_with(mock_config, mock_orchestrator)


@pytest.mark.asyncio
async def test_main_keyboard_interrupt():
    """Test main function handles KeyboardInterrupt gracefully."""
    test_args = ['main.py']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            mock_config_class.side_effect = KeyboardInterrupt()

            with patch('orchestrator.main.logging.getLogger') as mock_logger:
                mock_log = Mock()
                mock_logger.return_value = mock_log

                result = await main()

                assert result == 1
                mock_log.info.assert_called_with("Pipeline interrupted by user")


@pytest.mark.asyncio
async def test_main_exception_handling():
    """Test main function handles exceptions gracefully."""
    test_args = ['main.py']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            mock_config_class.side_effect = Exception("Test error")

            with patch('orchestrator.main.logging.getLogger') as mock_logger:
                mock_log = Mock()
                mock_logger.return_value = mock_log

                result = await main()

                assert result == 1
                mock_log.error.assert_called_once()
                error_call = mock_log.error.call_args[0][0]
                assert "Pipeline failed: Test error" in error_call


@pytest.mark.asyncio
async def test_main_directory_creation():
    """Test main function creates necessary directories."""
    test_args = ['main.py', '--stage', '1']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            with patch('orchestrator.main.setup_logging'):
                with patch('orchestrator.main.PipelineOrchestrator'):
                    with patch('orchestrator.main.run_stage1_discovery'):
                        mock_config = MockConfig()
                        mock_config_class.return_value = mock_config

                        # Mock Path.mkdir
                        with patch('pathlib.Path.mkdir') as mock_mkdir:
                            result = await main()

                            assert result == 0
                            # Should create directories for raw_dir, processed_dir, logs_dir
                            assert mock_mkdir.call_count >= 3
                            # Verify mkdir was called with parents=True, exist_ok=True
                            for call in mock_mkdir.call_args_list:
                                assert call[1]['parents'] is True
                                assert call[1]['exist_ok'] is True


@pytest.mark.parametrize("log_level", ["DEBUG", "INFO", "WARNING", "ERROR"])
@pytest.mark.asyncio
async def test_main_log_level_parameter(log_level):
    """Test main function respects log level parameter."""
    test_args = ['main.py', '--log-level', log_level, '--config-only']

    with patch('sys.argv', test_args):
        with patch('orchestrator.main.Config') as mock_config_class:
            with patch('orchestrator.main.setup_logging') as mock_setup_logging:
                with patch('builtins.print'):
                    mock_config = MockConfig()
                    mock_config._config = {}
                    mock_config_class.return_value = mock_config

                    result = await main()

                    assert result == 0
                    mock_setup_logging.assert_called_once()
                    call_args = mock_setup_logging.call_args
                    assert call_args[1]['log_level'] == log_level

