# TODO: Add support for running the pipeline in a distributed environment, such as using a task queue like Celery or RQ.
#!/usr/bin/env python3
"""
UConn Web Scraping Pipeline Orchestrator - The Master of All Web Scraping Dreams

This is the main orchestrator that does all the heavy lifting because apparently
we need one file to rule them all:
- Sets up logging (because we love knowing what went wrong)
- Loads YAML configuration (because JSON wasn't trendy enough)
- Launches discovery crawler (to find URLs like it's a treasure hunt)
- Spawns validation/enrichment workers (because parallel processing is fancy)
"""

import sys
import asyncio
import argparse
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# Add src to Python path because Python import system is a joy to work with
src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from src.orchestrator.config import Config, ConfigValidationError
from src.orchestrator.pipeline import PipelineOrchestrator
from src.common.logging import setup_logging
from src.common import config_keys as keys
from src.common.config_validator import validate_config_health

# module-level exports handled through lazy imports because dependencies are optional
# and we love making things complicated

# Import all the things we actually need
from scrapy.crawler import CrawlerProcess
from src.stage2.validator import URLValidator
from src.stage3.enrichment_spider import EnrichmentSpider


async def run_stage1_discovery(config: Config):
    """Run Stage 1: Discovery phase using Scrapy"""
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 1: DISCOVERY")
    logger.info("=" * 60)


    from scrapy.utils.project import get_project_settings
    from src.stage1.discovery_spider import DiscoverySpider

    # Get scrapy settings because configuration is always fun
    settings = get_project_settings()
    settings.update(config.get_scrapy_settings())

    # Configure stage 1 because we need more configuration
    stage1_config = config.get_stage1_config()

    # Get heuristics configuration
    heuristics = stage1_config.get(keys.DISCOVERY_HEURISTICS, {})

    # Get headless browser configuration
    headless_browser_config = stage1_config.get(keys.DISCOVERY_HEADLESS_BROWSER, {})

    settings.update({
        'STAGE1_OUTPUT_FILE': stage1_config[keys.DISCOVERY_OUTPUT_FILE],
        'SEED_FILE': stage1_config[keys.DISCOVERY_SEED_FILE],
        'USE_PERSISTENT_DEDUP': stage1_config.get(keys.DISCOVERY_USE_PERSISTENT_DEDUP, True),
        'DEDUP_CACHE_PATH': stage1_config.get(keys.DISCOVERY_DEDUP_CACHE_PATH, 'data/cache/url_cache.db'),
        # Heuristics feature flags
        'ENABLE_JSON_DISCOVERY': heuristics.get('enable_json_discovery', True),
        'ENABLE_AJAX_REGEX': heuristics.get('enable_ajax_regex', True),
        'ENABLE_PAGINATION_GUESS': heuristics.get('enable_pagination_guess', True),
        'ENABLE_DATA_ATTRIBUTE_DISCOVERY': heuristics.get('enable_data_attributes', True),
        'ENABLE_FORM_ACTION_DISCOVERY': heuristics.get('enable_form_actions', True),
        'ENABLE_META_REFRESH_DISCOVERY': heuristics.get('enable_meta_refresh', True),
        # Headless browser config for JavaScript-rendered content
        'HEADLESS_BROWSER_CONFIG': headless_browser_config,
        'ITEM_PIPELINES': {
            'src.stage1.discovery_pipeline.Stage1Pipeline': 300,  # Magic number for pipeline priority
        },
        'TELNETCONSOLE_ENABLED': False,  # Because nobody wants telnet in 2024
    })

    # Fire up the crawler because web scraping is what we do
    process = CrawlerProcess(settings)
    process.crawl(
        DiscoverySpider,
        max_depth=stage1_config[keys.DISCOVERY_MAX_DEPTH]
    )

    # Run scrapy in an executor because async is trendy
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, process.start)
    process.stop()  # Always clean up after yourself

    logger.info("Stage 1 discovery completed")


async def run_stage2_validation(config: Config, orchestrator: PipelineOrchestrator):
    """Run Stage 2: Validation phase - where we check if URLs actually work"""

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 2: VALIDATION")
    logger.info("=" * 60)

    # fixed deadlock because queues with limits are fun

    validator = URLValidator(config)

    # use the version that actually works
    await orchestrator.run_concurrent_stage2_validation(validator)


async def run_stage3_enrichment(config: Config, orchestrator: PipelineOrchestrator, use_async: bool = True):
    """Run Stage 3: Enrichment phase

    Args:
        config: Configuration object
        orchestrator: Pipeline orchestrator
        use_async: If True, use async processor (faster); if False, use Scrapy (traditional)
    """

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 3: ENRICHMENT")
    logger.info("=" * 60)

    if not use_async:
        logger.info("Async enrichment flag disabled; falling back to synchronous crawler execution")

    # set up stage 3 config
    stage3_config = config.get_stage3_config()
    scrapy_settings = {
        'ITEM_PIPELINES': {
            'src.stage3.enrichment_pipeline.Stage3Pipeline': 300,
        },
        'STAGE3_OUTPUT_FILE': stage3_config[keys.ENRICHMENT_OUTPUT_FILE],
        'LOG_LEVEL': config.get_logging_config()[keys.LOGGING_LEVEL],
        'ROBOTSTXT_OBEY': True,
        'DOWNLOAD_DELAY': 1,
        'CONCURRENT_REQUESTS': 16,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 8,
    }

    # Get NLP configuration
    nlp_config = config.get_nlp_config()

    spider_kwargs = {
        'content_types_config': stage3_config.get(keys.ENRICHMENT_CONTENT_TYPES, {}),
        'headless_browser_config': stage3_config.get(keys.ENRICHMENT_HEADLESS_BROWSER, {}),
        'allowed_domains': stage3_config.get(keys.ENRICHMENT_ALLOWED_DOMAINS, ['uconn.edu']),
    }
    if stage3_config.get(keys.ENRICHMENT_NLP_ENABLED, True):
        spider_kwargs['nlp_config'] = nlp_config

    # Run concurrent queue population and enrichment processing
    await orchestrator.run_concurrent_stage3_enrichment(
        EnrichmentSpider,
        scrapy_settings,
        spider_kwargs=spider_kwargs,
        use_async_processor=use_async
    )


def _setup_arg_parser() -> argparse.ArgumentParser:
    """Sets up and returns the argument parser for the CLI."""
    parser = argparse.ArgumentParser(description='UConn Web Scraping Pipeline Orchestrator')
    parser.add_argument(
        '--env',
        choices=['development', 'production'],
        default='development',
        help='Environment configuration to use'
    )
    parser.add_argument(
        '--stage',
        choices=['1', '2', '3', 'all'],
        default='all',
        help='Which stage(s) to run'
    )
    parser.add_argument(
        '--config-only',
        action='store_true',
        help='Only load and display configuration, do not run pipeline'
    )
    parser.add_argument(
        '--validate-only',
        action='store_true',
        help='Only validate configuration and run health checks, do not run pipeline'
    )
    parser.add_argument(
        '--async-enrichment',
        action='store_true',
        default=True,
        help='Use async enrichment processor (faster, default)'
    )
    parser.add_argument(
        '--no-async-enrichment',
        action='store_false',
        dest='async_enrichment',
        help='Use traditional Scrapy enrichment (slower)'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level'
    )
    return parser


def _cleanup_temp_directory(temp_dir: Path, max_age_hours: int = 24):
    """Clean up old temporary files from the temp directory.

    Args:
        temp_dir: Path to the temporary directory
        max_age_hours: Maximum age of files to keep (default: 24 hours)
    """
    logger = logging.getLogger(__name__)

    if not temp_dir.exists():
        return

    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    removed_count = 0
    removed_size = 0

    try:
        for item in temp_dir.iterdir():
            try:
                # Get file modification time
                mod_time = datetime.fromtimestamp(item.stat().st_mtime)

                if mod_time < cutoff_time:
                    if item.is_file():
                        size = item.stat().st_size
                        item.unlink()
                        removed_count += 1
                        removed_size += size
                    elif item.is_dir():
                        size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        shutil.rmtree(item)
                        removed_count += 1
                        removed_size += size
            except Exception as e:
                logger.warning(f"Failed to remove temp item {item}: {e}")

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} temp items ({removed_size / 1024 / 1024:.2f} MB)")
    except Exception as e:
        logger.error(f"Failed to clean temp directory: {e}")


def _initialize_pipeline(args: argparse.Namespace) -> Config:
    """Loads config, sets up logging, and creates data directories."""
    try:
        # Load and validate configuration
        config = Config(args.env)
    except ConfigValidationError as e:
        # Configuration validation failed - print error and exit
        print(f"\n{'=' * 80}")
        print("❌ Configuration Validation Failed")
        print(f"{'=' * 80}")
        print(f"\n{e}\n")
        print(f"{'=' * 80}\n")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n❌ Configuration file not found: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error loading configuration: {e}\n")
        sys.exit(1)

    data_paths = config.get_data_paths()
    setup_logging(log_level=args.log_level, log_dir=data_paths[keys.LOGS_DIR])

    logger = logging.getLogger(__name__)
    logger.info("Starting pipeline orchestrator")
    logger.info(f"Environment: {args.env}")
    logger.info(f"Stage(s): {args.stage}")

    # Create data directories if they don't exist
    for path in data_paths.values():
        path.mkdir(parents=True, exist_ok=True)

    # Run comprehensive health check
    logger.info("Running configuration health check...")
    is_healthy = validate_config_health(config)

    if not is_healthy:
        logger.error("Configuration health check failed. Please fix errors before continuing.")
        sys.exit(1)

    logger.info("Configuration health check passed [OK]")

    # Clean up old temporary files
    _cleanup_temp_directory(data_paths[keys.TEMP_DIR])

    return config


async def _run_pipeline_stages(args: argparse.Namespace, config: Config):
    """Runs the selected pipeline stages."""
    orchestrator = PipelineOrchestrator(config)

    if args.stage in ['1', 'all']:
        await run_stage1_discovery(config)

    if args.stage in ['2', 'all']:
        await run_stage2_validation(config, orchestrator)

    if args.stage in ['3', 'all']:
        await run_stage3_enrichment(config, orchestrator, use_async=args.async_enrichment)


async def main():
    """The main entry point for the pipeline orchestrator."""
    parser = _setup_arg_parser()
    args = parser.parse_args()

    config = _initialize_pipeline(args)

    if args.config_only:
        import yaml
        print("Configuration:\n" + yaml.dump(config._config, default_flow_style=False))
        return 0

    if args.validate_only:
        # Health check already ran in _initialize_pipeline
        print("\n[OK] Configuration validation completed successfully\n")
        return 0

    await _run_pipeline_stages(args, config)

    logger = logging.getLogger(__name__)
    logger.info("Pipeline orchestrator completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))