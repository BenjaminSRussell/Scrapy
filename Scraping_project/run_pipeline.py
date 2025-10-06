#!/usr/bin/env python3
"""Unified Pipeline CLI - Single entry point for all pipeline operations

Commands:
  run         Run the multi-stage pipeline (default)
  setup       Download and validate transformer models
  drain       Drain Delta Lake tables (keeps seed URLs)
  clean       Clean Delta Lake data
  reset       Reset pipeline to initial state
  export      Export Delta Lake tables
  validate    Validate pipeline setup
  health      Check pipeline health and statistics
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

# ============================================================================
# PIPELINE EXECUTION
# ============================================================================

async def run_scrapy_crawler():
    """Run Scrapy crawler in async context using subprocess."""
    logger.info("Starting Stage 1: Scrapy URL Discovery")

    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'scrapy', 'crawl', 'scout',
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # Stream output in real-time
    async def log_stream(stream, prefix):
        async for line in stream:
            logger.info(f"[{prefix}] {line.decode().strip()}")

    await asyncio.gather(
        log_stream(process.stdout, "SCRAPY"),
        log_stream(process.stderr, "SCRAPY-ERR")
    )

    await process.wait()
    logger.info(f"Scrapy crawler finished with code {process.returncode}")


async def run_js_bot():
    """Run JavaScript rendering bot for JS-heavy pages."""
    from src.stage1.js_bot import run_js_bot

    logger.info("Starting Stage 1: JS Bot for JavaScript-heavy pages")
    try:
        await run_js_bot()
        logger.info("JS Bot completed successfully")
    except Exception as e:
        logger.error(f"JS Bot failed: {e}", exc_info=True)


async def run_stage2_workers(num_workers: int = 100, batch_size: int = 200):
    """Run Stage 2 analytics workers with high concurrency."""
    logger.info(f"Starting Stage 2: {num_workers} analytics workers")

    worker = Stage2Worker(max_concurrent=num_workers, batch_size=batch_size)

    try:
        await worker.run()
        logger.info("Stage 2 workers completed successfully")
    except Exception as e:
        logger.error(f"Stage 2 workers failed: {e}", exc_info=True)


async def run_stage3_workers(num_workers: int = 50, batch_size: int = 100):
    """Run Stage 3 similarity detection & summarization workers."""
    logger.info(f"Starting Stage 3: {num_workers} summarization workers")

    worker = Stage3Worker(max_concurrent=num_workers, batch_size=batch_size)

    try:
        await worker.run()
        logger.info("Stage 3 workers completed successfully")
    except Exception as e:
        logger.error(f"Stage 3 workers failed: {e}", exc_info=True)


async def run_stage2_continuous(num_workers: int = 100, poll_interval: int = 3):
    """Run Stage 2 workers continuously, polling for new URLs."""
    logger.info(f"Starting Stage 2 continuous mode: {num_workers} workers, poll every {poll_interval}s")

    worker = Stage2Worker(max_concurrent=num_workers, batch_size=200)

    while True:
        try:
            all_urls = worker.delta.read('stage1_discovery')

            try:
                processed = worker.delta.read('stage2_page_analysis')
                processed_hashes = {r['url_hash'] for r in processed}
            except Exception:
                processed_hashes = set()

            pending = [
                url for url in all_urls
                if url.get('url_hash') not in processed_hashes
            ]

            if pending:
                logger.info(f"Stage 2 found {len(pending)} pending URLs to process")

                tasks = [worker._analyze_url(record) for record in pending[:100]]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

                if valid_results:
                    worker.delta.write('stage2_page_analysis', valid_results, mode='append', async_write=False)
                    logger.info(f"Stage 2 saved {len(valid_results)} results")
            else:
                logger.debug("Stage 2 waiting for new URLs...")

            await asyncio.sleep(poll_interval)

        except Exception as e:
            logger.error(f"Stage 2 continuous error: {e}", exc_info=True)
            await asyncio.sleep(poll_interval)


async def run_stage3_continuous(num_workers: int = 50, poll_interval: int = 5):
    """Run Stage 3 workers continuously, polling for quality documents."""
    logger.info(f"Starting Stage 3 continuous mode: {num_workers} workers, poll every {poll_interval}s")

    worker = Stage3Worker(max_concurrent=num_workers, batch_size=100)

    while True:
        try:
            all_docs = worker.delta.read('stage2_page_analysis')

            try:
                processed = worker.delta.read('stage3_summaries')
                processed_hashes = {r['url_hash'] for r in processed}
            except Exception:
                processed_hashes = set()

            quality_docs = [
                doc for doc in all_docs
                if not doc.get('is_low_quality', True)
                and not doc.get('is_massive_doc', False)
                and doc.get('url_hash') not in processed_hashes
            ]

            if quality_docs:
                logger.info(f"Stage 3 found {len(quality_docs)} quality documents to process")

                tasks = [worker._process_document(doc) for doc in quality_docs[:50]]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

                if valid_results:
                    worker.delta.write('stage3_summaries', valid_results, mode='append', async_write=False)
                    logger.info(f"Stage 3 saved {len(valid_results)} summaries")
            else:
                logger.debug("Stage 3 waiting for quality documents...")

            await asyncio.sleep(poll_interval)

        except Exception as e:
            logger.error(f"Stage 3 continuous error: {e}", exc_info=True)
            await asyncio.sleep(poll_interval)


async def orchestrate_pipeline(
    enable_scrapy: bool = True,
    enable_js_bot: bool = True,
    enable_stage2: bool = True,
    enable_stage3: bool = True,
    stage2_workers: int = 100,
    stage3_workers: int = 50,
    continuous_mode: bool = True
):
    """Orchestrate the entire async pipeline with concurrent execution."""
    logger.info("=" * 80)
    logger.info("ASYNC PIPELINE ORCHESTRATOR STARTING")
    logger.info(f"Mode: {'CONCURRENT' if continuous_mode else 'SEQUENTIAL'}")
    logger.info("=" * 80)

    if continuous_mode:
        tasks = []

        if enable_scrapy:
            tasks.append(asyncio.create_task(run_scrapy_crawler(), name="scrapy"))
        if enable_js_bot:
            tasks.append(asyncio.create_task(run_js_bot(), name="js-bot"))

        if enable_stage2:
            tasks.append(asyncio.create_task(run_stage2_continuous(stage2_workers), name="stage2-continuous"))

        if enable_stage3:
            tasks.append(asyncio.create_task(run_stage3_continuous(stage3_workers), name="stage3-continuous"))

        logger.info(f"Running {len(tasks)} concurrent tasks...")
        await asyncio.gather(*tasks, return_exceptions=True)

    else:
        tasks = []

        if enable_scrapy:
            tasks.append(asyncio.create_task(run_scrapy_crawler(), name="scrapy"))
        if enable_js_bot:
            tasks.append(asyncio.create_task(run_js_bot(), name="js-bot"))

        if tasks:
            logger.info("Waiting for Stage 1 (Discovery) to complete...")
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Stage 1 completed")

        if enable_stage2:
            logger.info("Starting Stage 2 (Analytics & QC)...")
            await run_stage2_workers(num_workers=stage2_workers)
            logger.info("Stage 2 completed")

        if enable_stage3:
            logger.info("Starting Stage 3 (Similarity & Summarization)...")
            await run_stage3_workers(num_workers=stage3_workers)
            logger.info("Stage 3 completed")

    logger.info("=" * 80)
    logger.info("PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


def cmd_run(args):
    """Run the pipeline."""
    try:
        asyncio.run(orchestrate_pipeline(
            enable_scrapy=not args.skip_stage1,
            enable_js_bot=not args.skip_stage1,
            enable_stage2=not args.skip_stage2,
            enable_stage3=not args.skip_stage3,
            stage2_workers=args.stage2_workers,
            stage3_workers=args.stage3_workers,
            continuous_mode=not args.sequential
        ))
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")

        # Force shutdown Delta Lake
        try:
            from src.common.delta_lake import get_delta_manager
            delta = get_delta_manager()
            delta.force_shutdown(timeout=15)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


# ============================================================================
# MODEL SETUP
# ============================================================================

def download_model(model_name: str, task: str = "summarization"):
    """Download and cache a transformer model."""
    try:
        from transformers import pipeline

        logger.info(f"Downloading model: {model_name}")
        logger.info("This may take several minutes depending on your connection...")

        pipe = pipeline(task, model=model_name, device=-1)

        logger.info(f"✅ Successfully downloaded and cached: {model_name}")
        del pipe
        return True

    except ImportError:
        logger.error("❌ transformers library not installed. Run: pip install transformers torch")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to download {model_name}: {e}")
        return False


def cmd_setup(args):
    """Download transformer models."""
    logger.info("=" * 80)
    logger.info("MODEL SETUP - Downloading Transformer Models")
    logger.info("=" * 80)

    models = [
        {
            "name": "sshleifer/distilbart-cnn-12-6",
            "purpose": "Stage 3 - Fast summarization (lightweight)",
            "size": "~350 MB"
        },
        {
            "name": "facebook/bart-large-cnn",
            "purpose": "Stage 4 - Heavy processing (large documents)",
            "size": "~1.6 GB"
        }
    ]

    logger.info(f"\nWill download {len(models)} models:")
    for i, model in enumerate(models, 1):
        logger.info(f"{i}. {model['name']}")
        logger.info(f"   Purpose: {model['purpose']}")
        logger.info(f"   Size: {model['size']}")

    logger.info(f"\nTotal download size: ~2 GB")
    logger.info(f"Models will be cached in: ~/.cache/huggingface/hub/")

    if not args.yes:
        try:
            response = input("\nProceed with download? [Y/n]: ").strip().lower()
            if response and response not in ['y', 'yes']:
                logger.info("Download cancelled.")
                return
        except (KeyboardInterrupt, EOFError):
            logger.info("\nDownload cancelled.")
            return

    logger.info("\nStarting downloads...\n")

    success_count = 0
    for i, model in enumerate(models, 1):
        logger.info(f"[{i}/{len(models)}] {model['name']}")
        if download_model(model['name']):
            success_count += 1
        logger.info("")

    logger.info("=" * 80)
    if success_count == len(models):
        logger.info(f"✅ SUCCESS - All {len(models)} models downloaded successfully!")
        logger.info("\nYou can now run the pipeline:")
        logger.info("  python run_pipeline.py run")
    else:
        logger.warning(f"⚠️  Downloaded {success_count}/{len(models)} models")
        logger.warning("Some models failed to download. Check errors above.")
        sys.exit(1)
    logger.info("=" * 80)


# ============================================================================
# DELTA LAKE OPERATIONS
# ============================================================================

def cmd_drain(args):
    """Drain Delta Lake tables without deleting seed URLs."""
    from scripts.drain_lake import drain_lake

    try:
        drain_lake(skip_confirmation=args.yes)
    except Exception as e:
        logger.error(f"Drain operation failed: {e}")
        sys.exit(1)


def cmd_clean(args):
    """Clean Delta Lake data."""
    project_root = Path(__file__).parent
    delta_lake_path = project_root / "data" / "delta_lake"

    print("=" * 80)
    print("DELTA LAKE CLEANUP")
    print("=" * 80)
    print(f"\nTarget: {delta_lake_path}")

    if delta_lake_path.exists():
        file_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_file())
        dir_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_dir())

        print(f"Current contents: {dir_count} directories, {file_count} files")
        print("\n⚠️  WARNING: This will PERMANENTLY DELETE all Delta Lake data!")
        print("This includes all stage tables and transaction logs.")

        if not args.yes:
            confirm = input("\nType 'yes' to confirm deletion: ")
            if confirm.lower() != 'yes':
                print("\n❌ Cleanup cancelled - no changes made")
                return

        print(f"\n🗑️  Draining Delta Lake at {delta_lake_path}...")

        try:
            shutil.rmtree(delta_lake_path)
            print("✅ Delta Lake data deleted successfully")

            delta_lake_path.mkdir(parents=True, exist_ok=True)
            print(f"✅ Empty Delta Lake directory recreated")

            print("\n" + "=" * 80)
            print("CLEANUP COMPLETE - Delta Lake is ready for a fresh start")
            print("=" * 80)

        except Exception as e:
            print(f"\n❌ ERROR: Failed to clean Delta Lake: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\n⚠️  Delta Lake directory not found - creating new directory")
        delta_lake_path.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created new Delta Lake directory at {delta_lake_path}")


def cmd_reset(args):
    """Reset pipeline to initial state."""
    project_root = Path(__file__).parent
    delta_lake_path = project_root / "data" / "delta_lake"
    seed_file = project_root / "data" / "seed_urls.csv"

    print("=" * 80)
    print("PIPELINE RESET")
    print("=" * 80)

    if not seed_file.exists():
        print(f"\n❌ ERROR: Seed file not found: {seed_file}")
        print("Please ensure seed_urls.csv exists before resetting.")
        sys.exit(1)

    with open(seed_file) as f:
        seed_count = sum(1 for line in f if line.strip() and line.strip().startswith('http'))

    print(f"\n📋 Seed file: {seed_file}")
    print(f"   URLs in seed: {seed_count}")

    if delta_lake_path.exists():
        file_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_file())
        dir_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_dir())
        print(f"\n📊 Current Delta Lake: {dir_count} directories, {file_count} files")
    else:
        print(f"\n📊 Current Delta Lake: Not initialized")

    print("\n⚠️  WARNING: This will DELETE all Delta Lake data and start fresh!")

    if not args.yes:
        confirm = input("\nType 'RESET' to confirm: ")
        if confirm != 'RESET':
            print("\n❌ Reset cancelled - no changes made")
            return

    print("\n🗑️  Cleaning Delta Lake...")

    try:
        if delta_lake_path.exists():
            shutil.rmtree(delta_lake_path)
            print("✅ Delta Lake data deleted")

        delta_lake_path.mkdir(parents=True, exist_ok=True)
        print(f"✅ Empty Delta Lake directory created")

        print("\n" + "=" * 80)
        print("✅ RESET COMPLETE - Pipeline is ready for a fresh run")
        print("=" * 80)
        print(f"\nNext steps:")
        print("  python run_pipeline.py run")

    except Exception as e:
        print(f"\n❌ ERROR: Reset failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_export(args):
    """Export Delta Lake tables."""
    try:
        import duckdb
        import pandas as pd
    except ImportError:
        print("❌ ERROR: Missing dependencies. Install with: pip install duckdb pandas")
        sys.exit(1)

    project_root = Path(__file__).parent
    delta_lake_path = project_root / "data" / "delta_lake"
    export_path = project_root / "exports"

    if not delta_lake_path.exists():
        print(f"❌ Delta Lake path not found: {delta_lake_path}")
        sys.exit(1)

    # List tables if requested
    if args.list:
        print("\n📚 Available Delta Lake tables:")
        print("-" * 80)

        con = duckdb.connect(database=':memory:')
        available_tables = [d.name for d in delta_lake_path.iterdir() if d.is_dir()]

        for name in sorted(available_tables):
            table_path = delta_lake_path / name
            parquet_files = list(table_path.glob("*.parquet"))

            if parquet_files:
                try:
                    query = f"SELECT COUNT(*) as count FROM read_parquet('{table_path}/*.parquet', union_by_name=True)"
                    count = con.execute(query).fetchone()[0]
                    print(f"  ✓ {name:30} ({count:,} rows, {len(parquet_files)} files)")
                except Exception as e:
                    print(f"  ✗ {name:30} (error: {e})")
            else:
                print(f"  ⚠ {name:30} (empty)")

        con.close()
        print("-" * 80)
        return

    # Export table(s)
    export_path.mkdir(parents=True, exist_ok=True)

    if args.all:
        available_tables = [d.name for d in delta_lake_path.iterdir() if d.is_dir()]
        print(f"=" * 80)
        print(f"EXPORTING ALL TABLES ({len(available_tables)} tables)")
        print("=" * 80)

        for table_name in sorted(available_tables):
            _export_table(table_name, args.format, delta_lake_path, export_path)
    else:
        if not args.table:
            print("❌ ERROR: Specify --table or use --all")
            sys.exit(1)
        _export_table(args.table, args.format, delta_lake_path, export_path, args.output)


def _export_table(table_name: str, format: str, delta_lake_path: Path, export_path: Path, output_file: str = None):
    """Export a single table."""
    import duckdb

    table_path = delta_lake_path / table_name

    if not table_path.exists():
        print(f"❌ Table '{table_name}' not found")
        return False

    parquet_files = list(table_path.glob("*.parquet"))
    if not parquet_files:
        print(f"❌ No data in '{table_name}'")
        return False

    try:
        con = duckdb.connect(database=':memory:')
        print(f"\n🔎 Reading '{table_name}'...")

        query = f"SELECT * FROM read_parquet('{table_path}/*.parquet', union_by_name=True)"
        result_df = con.execute(query).fetchdf()

        if result_df.empty:
            print(f"⚠️  No data in '{table_name}'")
            return False

        if output_file is None:
            output_file = export_path / f"{table_name}.{format}"
        else:
            output_file = Path(output_file)

        print(f"📊 {len(result_df)} rows, {len(result_df.columns)} columns")

        if format == "csv":
            result_df.to_csv(output_file, index=False)
        elif format == "json":
            result_df.to_json(output_file, orient='records', lines=True)
        elif format == "parquet":
            result_df.to_parquet(output_file, index=False)

        print(f"✅ Exported to: {output_file} ({output_file.stat().st_size / 1024 / 1024:.2f} MB)")
        con.close()
        return True

    except Exception as e:
        print(f"❌ Export failed: {e}")
        return False


# ============================================================================
# VALIDATION & HEALTH
# ============================================================================

def cmd_validate(args):
    """Validate pipeline setup."""
    project_root = Path(__file__).parent
    all_ok = True

    print("=" * 80)
    print("PIPELINE SETUP VALIDATION")
    print("=" * 80)

    # Check dependencies
    print("\n📦 Dependencies:")
    deps = [
        ('scrapy', 'Scrapy'),
        ('deltalake', 'Delta Lake'),
        ('duckdb', 'DuckDB'),
        ('httpx', 'HTTPX'),
        ('datasketch', 'datasketch'),
        ('transformers', 'Transformers'),
    ]

    for module_name, display_name in deps:
        try:
            module = __import__(module_name)
            version = getattr(module, '__version__', 'installed')
            print(f"  ✅ {display_name}: {version}")
        except ImportError:
            print(f"  ❌ {display_name} not installed")
            all_ok = False

    # Check core files
    print("\n📜 Core Files:")
    core_files = [
        ('src/stage1/scout_spider.py', 'Stage 1: Scout Spider'),
        ('src/stage2/stage2_worker.py', 'Stage 2: Worker'),
        ('src/stage3/stage3_worker.py', 'Stage 3: Worker'),
        ('src/common/delta_lake.py', 'Delta Lake Manager'),
    ]

    for file_path, desc in core_files:
        if (project_root / file_path).exists():
            print(f"  ✅ {desc}")
        else:
            print(f"  ❌ {desc} - NOT FOUND")
            all_ok = False

    # Check data directories
    print("\n📁 Data Directories:")
    for dir_path in ['data', 'data/delta_lake']:
        path = project_root / dir_path
        if path.is_dir():
            print(f"  ✅ {dir_path}")
        else:
            print(f"  ⚠️  {dir_path} - will be created")

    print("\n" + "=" * 80)
    if all_ok:
        print("✅ VALIDATION PASSED - Pipeline is ready!")
        print("\nNext: python run_pipeline.py setup  # Download models")
    else:
        print("❌ VALIDATION FAILED - Install missing dependencies:")
        print("  pip install -e .")
    print("=" * 80)


def cmd_health(args):
    """Check pipeline health and statistics."""
    from src.common.delta_lake import get_delta_manager

    print("=" * 80)
    print("PIPELINE HEALTH CHECK")
    print("=" * 80)

    try:
        delta = get_delta_manager()

        print("\n📊 DELTA LAKE STATISTICS:")
        tables = ['stage1_discovery', 'stage2_page_analysis', 'stage3_summaries', 'stage4_summaries']

        for table_name in tables:
            try:
                data = delta.read(table_name)
                print(f"  ✅ {table_name}: {len(data):,} records")
            except Exception:
                print(f"  ⚠️  {table_name}: no data")

        print("\n⚙️  SYSTEM STATUS:")
        print("  ✅ Delta Lake: Operational")

        # Check models
        try:
            from transformers import AutoTokenizer
            AutoTokenizer.from_pretrained("sshleifer/distilbart-cnn-12-6")
            print("  ✅ Models: Downloaded")
        except Exception:
            print("  ⚠️  Models: Not downloaded (run: python run_pipeline.py setup)")

        print("\n" + "=" * 80)

    except Exception as e:
        print(f"\n❌ Health check failed: {e}")
        sys.exit(1)


# ============================================================================
# CLI SETUP
# ============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Unified Pipeline CLI - UConn Web Scraping Pipeline",
        formatter_class=argparse.RawTextHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # RUN command
    parser_run = subparsers.add_parser('run', help='Run the pipeline')
    parser_run.add_argument('--sequential', action='store_true', help='Run stages sequentially (default: concurrent)')
    parser_run.add_argument('--skip-stage1', action='store_true', help='Skip Stage 1 (Discovery)')
    parser_run.add_argument('--skip-stage2', action='store_true', help='Skip Stage 2 (Analysis)')
    parser_run.add_argument('--skip-stage3', action='store_true', help='Skip Stage 3 (Summarization)')
    parser_run.add_argument('--stage2-workers', type=int, default=100, help='Stage 2 worker count (default: 100)')
    parser_run.add_argument('--stage3-workers', type=int, default=50, help='Stage 3 worker count (default: 50)')
    parser_run.set_defaults(func=cmd_run)

    # SETUP command
    parser_setup = subparsers.add_parser('setup', help='Download transformer models')
    parser_setup.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    parser_setup.set_defaults(func=cmd_setup)

    # DRAIN command
    parser_drain = subparsers.add_parser('drain', help='Drain Delta Lake tables (keeps seed URLs)')
    parser_drain.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    parser_drain.set_defaults(func=cmd_drain)

    # CLEAN command
    parser_clean = subparsers.add_parser('clean', help='Clean Delta Lake data')
    parser_clean.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    parser_clean.set_defaults(func=cmd_clean)

    # RESET command
    parser_reset = subparsers.add_parser('reset', help='Reset pipeline to initial state')
    parser_reset.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    parser_reset.set_defaults(func=cmd_reset)

    # EXPORT command
    parser_export = subparsers.add_parser('export', help='Export Delta Lake tables')
    parser_export.add_argument('--table', type=str, help='Table name to export')
    parser_export.add_argument('--all', action='store_true', help='Export all tables')
    parser_export.add_argument('--format', choices=['csv', 'json', 'parquet'], default='csv', help='Output format')
    parser_export.add_argument('--output', type=str, help='Output file path')
    parser_export.add_argument('--list', action='store_true', help='List available tables')
    parser_export.set_defaults(func=cmd_export)

    # VALIDATE command
    parser_validate = subparsers.add_parser('validate', help='Validate pipeline setup')
    parser_validate.set_defaults(func=cmd_validate)

    # HEALTH command
    parser_health = subparsers.add_parser('health', help='Check pipeline health')
    parser_health.set_defaults(func=cmd_health)

    args = parser.parse_args()

    # Default to 'run' if no command specified
    if not args.command:
        args.command = 'run'
        args.func = cmd_run
        args.sequential = False
        args.skip_stage1 = False
        args.skip_stage2 = False
        args.skip_stage3 = False
        args.stage2_workers = 100
        args.stage3_workers = 50

    # Execute command
    args.func(args)


if __name__ == "__main__":
    main()
