#!/usr/bin/env python3
"""
UConn Web Scraping Pipeline Orchestrator - Single Entry Point

This is the main orchestrator that:
- Sets up logging
- Loads YAML configuration
- Launches discovery crawler
- Spawns validation/enrichment workers
"""

import sys
import asyncio
import argparse
import logging
from pathlib import Path

# Add src to Python path
src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from orchestrator.config import Config
from orchestrator.pipeline import PipelineOrchestrator
from common.logging import setup_logging

# module-level exports handled through lazy imports because dependencies are optional

try:  # pragma: no cover - optional dependency for tests
    from scrapy.crawler import CrawlerProcess as _CrawlerProcess
except ImportError:  # pragma: no cover - tests patch the attribute
    _CrawlerProcess = None

try:  # pragma: no cover - optional dependency for tests
    from stage2.validator import URLValidator as _URLValidator
except ImportError:  # pragma: no cover
    _URLValidator = None

try:  # pragma: no cover - optional dependency for tests
    from stage3.enrichment_spider import EnrichmentSpider as _EnrichmentSpider
except ImportError:  # pragma: no cover
    _EnrichmentSpider = None

# Expose patchable symbols for tests; fall back to lazy imports at runtime
CrawlerProcess = _CrawlerProcess
URLValidator = _URLValidator
EnrichmentSpider = _EnrichmentSpider


async def run_stage1_discovery(config: Config):
    """Run Stage 1: Discovery phase using Scrapy"""
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 1: DISCOVERY")
    logger.info("=" * 60)

    process_class = CrawlerProcess
    if process_class is None:  # Lazy import when Scrapy is installed
        from scrapy.crawler import CrawlerProcess as process_class

    from scrapy.utils.project import get_project_settings
    from stage1.discovery_spider import DiscoverySpider

    settings = get_project_settings()
    settings.update(config.get_scrapy_settings())

    stage1_config = config.get_stage1_config()
    settings.update({
        'STAGE1_OUTPUT_FILE': stage1_config['output_file'],
        'ITEM_PIPELINES': {
            'stage1.discovery_pipeline.Stage1Pipeline': 300,
        },
        'TELNETCONSOLE_ENABLED': False,
    })

    process = process_class(settings)
    process.crawl(
        DiscoverySpider,
        max_depth=stage1_config['max_depth']
    )

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, process.start)
    finally:
        process.stop()

    logger.info("Stage 1 discovery completed")


async def run_stage2_validation(config: Config, orchestrator: PipelineOrchestrator):
    """Run Stage 2: Validation phase"""
    validator_class = URLValidator
    if validator_class is None:  # Lazy import in production
        from stage2.validator import URLValidator as validator_class

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 2: VALIDATION")
    logger.info("=" * 60)

    # fixed deadlock because queues with limits are fun

    validator = validator_class(config)

    # use the version that actually works
    await orchestrator.run_concurrent_stage2_validation(validator)


async def run_stage3_enrichment(config: Config, orchestrator: PipelineOrchestrator):
    """Run Stage 3: Enrichment phase"""
    spider_class = EnrichmentSpider
    if spider_class is None:  # Lazy import in production
        from stage3.enrichment_spider import EnrichmentSpider as spider_class

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("STAGE 3: ENRICHMENT")
    logger.info("=" * 60)

    # set up stage 3 config
    stage3_config = config.get_stage3_config()
    scrapy_settings = {
        'ITEM_PIPELINES': {
            'stage3.enrichment_pipeline.Stage3Pipeline': 300,
        },
        'STAGE3_OUTPUT_FILE': stage3_config['output_file'],
        'LOG_LEVEL': config.get_logging_config()['level'],
        'ROBOTSTXT_OBEY': True,
        'DOWNLOAD_DELAY': 1,
        'CONCURRENT_REQUESTS': 16,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 8,
    }

    # make the spider
    enricher = spider_class()

    # Run concurrent queue population and enrichment processing
    await orchestrator.run_concurrent_stage3_enrichment(enricher, scrapy_settings)


async def main():
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
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level'
    )

    args = parser.parse_args()

    logger = logging.getLogger(__name__)

    try:
        # load config
        config = Config(args.env)

        # set up logging
        data_paths = config.get_data_paths()
        setup_logging(
            log_level=args.log_level,
            log_dir=data_paths['logs_dir']
        )

        logger.info(f"Starting pipeline orchestrator")
        logger.info(f"Environment: {args.env}")
        logger.info(f"Stage(s): {args.stage}")

        if args.config_only:
            import yaml
            print("Configuration:\n" + yaml.dump(config._config, default_flow_style=False))
            return 0

        # make folders
        for path in data_paths.values():
            path.mkdir(parents=True, exist_ok=True)

        # start orchestrator
        orchestrator = PipelineOrchestrator(config)

        # run the stages
        if args.stage in ['1', 'all']:
            await run_stage1_discovery(config)

        if args.stage in ['2', 'all']:
            await run_stage2_validation(config, orchestrator)

        if args.stage in ['3', 'all']:
            await run_stage3_enrichment(config, orchestrator)

        logger.info("Pipeline orchestrator completed successfully")
        return 0

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
