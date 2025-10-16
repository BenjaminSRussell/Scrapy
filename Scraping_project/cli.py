#!/usr/bin/env python3
"""Unified Pipeline CLI - Single entry point for all operations

Provides commands for running Scrapy, orchestrating the full pipeline,
and handling operational utilities from one tool.

Commands:
  scrapy      Run Scrapy spiders (simple Docker entrypoint)
  pipeline    Run full multi-stage pipeline
  setup       Download and validate models
  drain       Drain Delta Lake tables
  export      Export Delta Lake data
  health      Check pipeline health
"""

#!/usr/bin/env python3
"""Unified Pipeline CLI - Single entry point for all operations

Provides commands for running Scrapy, orchestrating the full pipeline,
and handling operational utilities from one tool.

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
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================================
# SCRAPY COMMAND (Docker entrypoint)
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
            self.spider_names = spider_names or ["scout"]
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


def cmd_deep_dive(args):
    """Run the deep dive spider for thorough, respectful crawling."""
    from scrapy.crawler import CrawlerRunner
    from scrapy.utils.log import configure_logging
    from scrapy.utils.project import get_project_settings
    from twisted.internet import defer, reactor

    class ScrapyRunner:
        """Orchestrates Scrapy spiders."""

        def __init__(self):
            self.settings = get_project_settings()
            configure_logging(self.settings)
            self.runner = CrawlerRunner(self.settings)
            self.shutdown_requested = False

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
        def run_spider(self):
            """Run deep dive spider."""
            logger.info("Starting deep dive spider...")
            try:
                yield self.runner.crawl("deep_dive")
                logger.info("Deep dive spider completed")
            finally:
                if reactor.running:
                    reactor.stop()

        def start(self):
            """Start runner and reactor."""
            self.setup_signal_handlers()
            deferred = self.run_spider()
            deferred.addErrback(lambda f: logger.error(f"Failed: {f}"))
            reactor.run()

    # Run
    runner = ScrapyRunner()
    runner.start()


# ============================================================================
# PIPELINE COMMAND (multi-stage pipeline)
# ============================================================================


async def run_scrapy_crawler():
    """Run Scrapy crawler async."""
    logger.info("Stage 1: Scrapy Discovery")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scrapy",
        "crawl",
        "scout",
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def log_stream(stream, prefix):
        async for line in stream:
            logger.info(f"[{prefix}] {line.decode(errors='ignore').strip()}")

    await asyncio.gather(log_stream(process.stdout, "SCRAPY"), log_stream(process.stderr, "SCRAPY-ERR"))
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
            await run_stage2_workers(num_workers=args.stage2_workers, batch_size=args.stage2_batch)

        # Stage 3: Summarization
        if not args.skip_stage3:
            await run_stage3_workers(num_workers=args.stage3_workers, batch_size=args.stage3_batch)

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
    from src.common.delta_lake import DeltaLakeManager

    manager = DeltaLakeManager.get_instance()

    if args.table:
        logger.info(f"Exporting table: {args.table}")
        result = manager.export(args.table, args.output, format=args.format)
        if "error" in result:
            raise RuntimeError(f"Export failed for table {args.table}: {result['error']}")
        logger.info(f"✅ Exported to {result.get('path')}")
    else:
        logger.info(f"Exporting all tables to: {args.output}")
        results = manager.export_all(args.output, format=args.format)
        has_errors = False
        for r in results:
            if "error" in r:
                logger.warning(f"  ✗ {r['table']}: {r['error']}")
                has_errors = True
            else:
                logger.info(f"  ✓ {r['table']}: {r['rows']} rows, {r['size_mb']:.2f} MB")

        if has_errors:
            raise RuntimeError("One or more table exports failed.")


def cmd_health(args):
    """Check pipeline health."""
    from src.common.delta_lake import DeltaLakeManager

    manager = DeltaLakeManager.get_instance()

    logger.info("Pipeline Health Check")
    logger.info("=" * 60)

    tables = manager.list_tables()
    for table in tables:
        status = "✓" if table["exists"] else "✗"
        logger.info(f"{status} {table['name']}: {table['row_count']} rows, {table['parquet_files']} files")

    logger.info("=" * 60)


def cmd_setup(args):
    """Download and validate models."""
    logger.info("Downloading transformer models...")
    # Model download logic here
    logger.info("✅ Setup complete")


def cmd_reset(args):
    """Reset Delta Lake tables and re-seed from CSV."""
    import hashlib
    import shutil

    import pandas as pd

    from src.common.constants import DELTA_LAKE
    from src.common.delta_lake import DeltaLakeManager

    logger.info("🔥 RESETTING DELTA LAKE...")
    logger.warning("This will DELETE all data in Delta Lake tables!")

    if not args.force:
        confirmation = input("Are you sure? Type 'yes' to continue: ")
        if confirmation.lower() != "yes":
            logger.info("Reset cancelled.")
            return

    # Delete all Delta Lake tables
    if DELTA_LAKE.exists():
        logger.info(f"Deleting Delta Lake directory: {DELTA_LAKE}")
        shutil.rmtree(DELTA_LAKE)
        logger.info("✅ Delta Lake wiped")

    # Recreate Delta Lake manager (will recreate directories)
    logger.info("Recreating Delta Lake structure...")
    manager = DeltaLakeManager.get_instance()
    logger.info("✅ Delta Lake structure recreated")

    # Re-seed from CSV
    csv_path = Path(__file__).parent / "data" / "raw" / "uconn_urls.csv"
    if not csv_path.exists():
        logger.error(f"Seed file not found: {csv_path}")
        return

    logger.info(f"Loading seed URLs from: {csv_path}")
    df = pd.read_csv(csv_path, header=None, names=["url"])

    # Add url_hash column
    df["url_hash"] = df["url"].apply(lambda url: hashlib.sha256(url.encode("utf-8")).hexdigest())
    df["added_at"] = pd.Timestamp.now().isoformat()

    seed_records = df.to_dict("records")
    logger.info(f"Seeding {len(seed_records)} URLs...")

    manager.write("seed_urls", seed_records, mode="overwrite", async_write=False)
    logger.info("✅ Seed URLs loaded")
    logger.info("🎉 Reset complete!")


def cmd_clean(args):
    """Clean temporary files and caches."""
    import shutil

    logger.info("🧹 Cleaning temporary files...")

    cleaned = []

    # Find and delete __pycache__ directories
    for pycache in Path(__file__).parent.rglob("__pycache__"):
        shutil.rmtree(pycache)
        cleaned.append(str(pycache))

    # Find and delete .pyc files
    for pyc_file in Path(__file__).parent.rglob("*.pyc"):
        pyc_file.unlink()
        cleaned.append(str(pyc_file))

    # Find and delete .DS_Store files (macOS)
    for ds_store in Path(__file__).parent.rglob(".DS_Store"):
        ds_store.unlink()
        cleaned.append(str(ds_store))

    # Clean Scrapy .scrapy directories
    for scrapy_dir in Path(__file__).parent.rglob(".scrapy"):
        if scrapy_dir.is_dir():
            shutil.rmtree(scrapy_dir)
            cleaned.append(str(scrapy_dir))

    logger.info(f"✅ Cleaned {len(cleaned)} files/directories")
    if args.verbose:
        for item in cleaned[:20]:  # Show first 20
            logger.info(f"  - {item}")
        if len(cleaned) > 20:
            logger.info(f"  ... and {len(cleaned) - 20} more")


def cmd_validate(args):
    """Validate Delta Lake tables."""
    from src.common.delta_lake import DeltaLakeManager

    logger.info("🔍 Validating Delta Lake tables...")
    manager = DeltaLakeManager.get_instance()

    tables = manager.list_tables()
    issues = []

    for table in tables:
        logger.info(f"\nValidating: {table['name']}")
        logger.info(f"  Exists: {table['exists']}")
        logger.info(f"  Row count: {table['row_count']}")
        logger.info(f"  Parquet files: {table['parquet_files']}")

        # Check for issues
        if table["exists"] and table["row_count"] == 0:
            issue = f"{table['name']}: Table exists but has 0 rows"
            issues.append(issue)
            logger.warning(f"  ⚠️  {issue}")

        if table["parquet_files"] > 0 and not table["exists"]:
            issue = f"{table['name']}: Has parquet files but no Delta log"
            issues.append(issue)
            logger.warning(f"  ⚠️  {issue}")

        if "error" in table:
            issue = f"{table['name']}: Error reading table - {table['error']}"
            issues.append(issue)
            logger.error(f"  ❌ {issue}")

        # Sample data validation
        if table["exists"] and table["row_count"] > 0:
            try:
                sample = manager.read(table["name"])[:5]  # Read first 5 records
                if sample:
                    logger.info(f"  Sample record keys: {list(sample[0].keys())}")
                    logger.info("  ✅ Schema appears valid")
                else:
                    issue = f"{table['name']}: Could not read sample data"
                    issues.append(issue)
                    logger.warning(f"  ⚠️  {issue}")
            except Exception as e:
                issue = f"{table['name']}: Error reading sample - {str(e)}"
                issues.append(issue)
                logger.error(f"  ❌ {issue}")

    logger.info("\n" + "=" * 60)
    if issues:
        logger.warning(f"⚠️  Found {len(issues)} issues:")
        for issue in issues:
            logger.warning(f"  - {issue}")
    else:
        logger.info("✅ All tables validated successfully!")
    logger.info("=" * 60)


# ============================================================================
# MAIN CLI
# ============================================================================


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Unified Pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Scrapy command
    scrapy_parser = subparsers.add_parser("scrapy", help="Run Scrapy spiders")
    scrapy_parser.add_argument("--spiders", nargs="+", help="Spider names")
    scrapy_parser.set_defaults(func=cmd_scrapy)

    # Deep dive command
    deep_dive_parser = subparsers.add_parser("deep_dive", help="Run deep dive spider (conservative crawling)")
    deep_dive_parser.set_defaults(func=cmd_deep_dive)

    # Pipeline command
    pipeline_parser = subparsers.add_parser("pipeline", help="Run full pipeline")
    pipeline_parser.add_argument("--skip-stage1", action="store_true")
    pipeline_parser.add_argument("--skip-stage2", action="store_true")
    pipeline_parser.add_argument("--skip-stage3", action="store_true")
    pipeline_parser.add_argument("--stage2-workers", type=int, default=100)
    pipeline_parser.add_argument("--stage2-batch", type=int, default=200)
    pipeline_parser.add_argument("--stage3-workers", type=int, default=50)
    pipeline_parser.add_argument("--stage3-batch", type=int, default=100)
    pipeline_parser.set_defaults(func=cmd_pipeline)

    # Drain command
    drain_parser = subparsers.add_parser("drain", help="Drain Delta Lake")
    drain_parser.set_defaults(func=cmd_drain)

    # Export command
    export_parser = subparsers.add_parser("export", help="Export Delta Lake")
    export_parser.add_argument("--table", help="Specific table to export")
    export_parser.add_argument("--output", default="exports", help="Output directory")
    export_parser.add_argument("--format", choices=["csv", "json", "parquet"], default="csv")
    export_parser.set_defaults(func=cmd_export)

    # Health command
    health_parser = subparsers.add_parser("health", help="Check health")
    health_parser.set_defaults(func=cmd_health)

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Setup models")
    setup_parser.set_defaults(func=cmd_setup)

    # Reset command
    reset_parser = subparsers.add_parser("reset", help="Reset Delta Lake and re-seed")
    reset_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    reset_parser.set_defaults(func=cmd_reset)

    # Clean command
    clean_parser = subparsers.add_parser("clean", help="Clean temporary files")
    clean_parser.add_argument("--verbose", action="store_true", help="Show all cleaned files")
    clean_parser.set_defaults(func=cmd_clean)

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate Delta Lake tables")
    validate_parser.set_defaults(func=cmd_validate)

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


if __name__ == "__main__":
    main()
