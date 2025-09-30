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
from pathlib import Path

# Add src to Python path because Python import system is a joy to work with
src_dir = Path(__file__).parent.parent
sys.path.insert(0, str(src_dir))

from src.orchestrator.config import Config
from src.orchestrator.pipeline import PipelineOrchestrator
from src.common.logging import setup_logging

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
    settings.update({
        'STAGE1_OUTPUT_FILE': stage1_config['output_file'],
        'ITEM_PIPELINES': {
            'src.stage1.discovery_pipeline.Stage1Pipeline': 300,  # Magic number for pipeline priority
        },
        'TELNETCONSOLE_ENABLED': False,  # Because nobody wants telnet in 2024
    })

    # Fire up the crawler because web scraping is what we do
    process = CrawlerProcess(settings)
    process.crawl(
        DiscoverySpider,
        max_depth=stage1_config['max_depth']  # Don't go too deep, we're not archaeologists
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


async def run_stage3_enrichment(config: Config, orchestrator: PipelineOrchestrator):
    """Run Stage 3: Enrichment phase"""

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
    enricher = EnrichmentSpider()

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

    config = Config(args.env)

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

    for path in data_paths.values():
        path.mkdir(parents=True, exist_ok=True)

    orchestrator = PipelineOrchestrator(config)

    if args.stage in ['1', 'all']:
        await run_stage1_discovery(config)

    if args.stage in ['2', 'all']:
        await run_stage2_validation(config, orchestrator)

    if args.stage in ['3', 'all']:
        await run_stage3_enrichment(config, orchestrator)

    logger.info("Pipeline orchestrator completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
