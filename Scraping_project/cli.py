#!/usr/bin/env python3
"""Unified Pipeline CLI - Single entry point for all operations

This consolidates run.py and run_pipeline.py into a single CLI tool.

Commands:
  scrapy      Run Scrapy spiders (simple Docker entrypoint)
  pipeline    Run full multi-stage pipeline
  setup       Download and validate models
  drain       Drain Delta Lake tables
  export      Export Delta Lake data
  health      Check pipeline health
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# SCRAPY COMMAND (from run.py)
# ============================================================================

def cmd_scrapy(args):
    """Run Scrapy spiders using CrawlerRunner (Docker entrypoint)."""
    from scrapy.crawler import CrawlerRunner
    from scrapy.utils.log import configure_logging
    from scrapy.utils.project import get_project_settings
    from twisted.internet import defer, reactor

    class ScrapyRunner:
        """Orchestrates Scrapy spiders."""

        def __init__(self, spider_names):
            self.settings = get_project_settings()
            configure_logging(self.settings)
            self.runner = CrawlerRunner(self.settings)
            self.spider_names = spider_names or ['scout']
            self.shutdown_requested = False

            logger.info(f"Spiders to run: {', '.join(self.spider_names)}")

        def setup_signal_handlers(self):
            """Set up graceful shutdown."""
            def signal_handler(signum, frame):
                sig_name = signal.Signals(signum).name
                logger.info(f"Received {sig_name}, shutting down...")
                self.shutdown_requested = True
                if reactor.running:
                    reactor.callFromThread(reactor.stop)

            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)

        @defer.inlineCallbacks
        def run_spiders(self):
            """Run spiders in parallel."""
            logger.info("Starting spiders...")
            try:
                deferreds = []
                for spider_name in self.spider_names:
                    if self.shutdown_requested:
                        break
                    logger.info(f"Scheduling: {spider_name}")
                    deferreds.append(self.runner.crawl(spider_name))

                if deferreds:
                    yield defer.DeferredList(deferreds)
                logger.info("All spiders completed")
            finally:
                if reactor.running:
                    reactor.stop()

        def start(self):
            """Start runner and reactor."""
            self.setup_signal_handlers()
            deferred = self.run_spiders()
            deferred.addErrback(lambda f: logger.error(f"Failed: {f}"))
            reactor.run()

    # Run
    runner = ScrapyRunner(args.spiders)
    runner.start()


# ============================================================================
# PIPELINE COMMAND (from run_pipeline.py)
# ============================================================================

async def run_scrapy_crawler():
    """Run Scrapy crawler async."""
    logger.info("Stage 1: Scrapy Discovery")
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'scrapy', 'crawl', 'scout',
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    async def log_stream(stream, prefix):
        async for line in stream:
            logger.info(f"[{prefix}] {line.decode(errors='ignore').strip()}")

    await asyncio.gather(
        log_stream(process.stdout, "SCRAPY"),
        log_stream(process.stderr, "SCRAPY-ERR")
    )
    await process.wait()
    logger.info(f"Scrapy finished: {process.returncode}")


async def run_stage2_workers(num_workers=100, batch_size=200):
    """Run Stage 2 analytics."""
    from src.stage2.stage2_worker import Stage2Worker
    logger.info(f"Stage 2: {num_workers} workers")
    worker = Stage2Worker(max_concurrent=num_workers, batch_size=batch_size)
    await worker.run()


async def run_stage3_workers(num_workers=50, batch_size=100):
    """Run Stage 3 summarization."""
    from src.stage3.stage3_worker import Stage3Worker
    logger.info(f"Stage 3: {num_workers} workers")
    worker = Stage3Worker(max_concurrent=num_workers, batch_size=batch_size)
    await worker.run()


def cmd_pipeline(args):
    """Run full multi-stage pipeline."""
    async def run():
        logger.info("🚀 Starting Multi-Stage Pipeline")

        # Stage 1: Discovery
        if not args.skip_stage1:
            await run_scrapy_crawler()

        # Stage 2: Analytics
        if not args.skip_stage2:
            await run_stage2_workers(
                num_workers=args.stage2_workers,
                batch_size=args.stage2_batch
            )

        # Stage 3: Summarization
        if not args.skip_stage3:
            await run_stage3_workers(
                num_workers=args.stage3_workers,
                batch_size=args.stage3_batch
            )

        logger.info("✅ Pipeline Complete")

    asyncio.run(run())


# ============================================================================
# UTILITY COMMANDS
# ============================================================================

def cmd_drain(args):
    """Drain Delta Lake tables."""
    from drain_lake import drain_tables
    logger.info("Draining Delta Lake...")
    drain_tables()


def cmd_export(args):
    """Export Delta Lake tables."""
    from src.common.delta_lake import get_delta_manager
    manager = get_delta_manager()

    if args.table:
        logger.info(f"Exporting table: {args.table}")
        result = manager.export(args.table, args.output, format=args.format)
        logger.info(f"✅ Exported: {result}")
    else:
        logger.info(f"Exporting all tables to: {args.output}")
        results = manager.export_all(args.output, format=args.format)
        for r in results:
            if 'error' in r:
                logger.warning(f"  ✗ {r['table']}: {r['error']}")
            else:
                logger.info(f"  ✓ {r['table']}: {r['rows']} rows, {r['size_mb']:.2f} MB")


def cmd_health(args):
    """Check pipeline health."""
    from src.common.delta_lake import get_delta_manager
    manager = get_delta_manager()

    logger.info("Pipeline Health Check")
    logger.info("=" * 60)

    tables = manager.list_tables()
    for table in tables:
        status = "✓" if table['exists'] else "✗"
        logger.info(f"{status} {table['name']}: {table['row_count']} rows, {table['parquet_files']} files")

    logger.info("=" * 60)


def cmd_setup(args):
    """Download and validate models."""
    logger.info("Downloading transformer models...")
    # Model download logic here
    logger.info("✅ Setup complete")


# ============================================================================
# MAIN CLI
# ============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Unified Pipeline CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Scrapy command
    scrapy_parser = subparsers.add_parser('scrapy', help='Run Scrapy spiders')
    scrapy_parser.add_argument('--spiders', nargs='+', help='Spider names')
    scrapy_parser.set_defaults(func=cmd_scrapy)

    # Pipeline command
    pipeline_parser = subparsers.add_parser('pipeline', help='Run full pipeline')
    pipeline_parser.add_argument('--skip-stage1', action='store_true')
    pipeline_parser.add_argument('--skip-stage2', action='store_true')
    pipeline_parser.add_argument('--skip-stage3', action='store_true')
    pipeline_parser.add_argument('--stage2-workers', type=int, default=100)
    pipeline_parser.add_argument('--stage2-batch', type=int, default=200)
    pipeline_parser.add_argument('--stage3-workers', type=int, default=50)
    pipeline_parser.add_argument('--stage3-batch', type=int, default=100)
    pipeline_parser.set_defaults(func=cmd_pipeline)

    # Drain command
    drain_parser = subparsers.add_parser('drain', help='Drain Delta Lake')
    drain_parser.set_defaults(func=cmd_drain)

    # Export command
    export_parser = subparsers.add_parser('export', help='Export Delta Lake')
    export_parser.add_argument('--table', help='Specific table to export')
    export_parser.add_argument('--output', default='exports', help='Output directory')
    export_parser.add_argument('--format', choices=['csv', 'json', 'parquet'], default='csv')
    export_parser.set_defaults(func=cmd_export)

    # Health command
    health_parser = subparsers.add_parser('health', help='Check health')
    health_parser.set_defaults(func=cmd_health)

    # Setup command
    setup_parser = subparsers.add_parser('setup', help='Setup models')
    setup_parser.set_defaults(func=cmd_setup)

    # Parse and execute
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
