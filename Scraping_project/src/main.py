#!/usr/bin/env python3
"""
Simplified pipeline runner - no orchestration bloat.
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.constants import LOGS_DIR, LOG_FORMAT
from src.common.delta_storage import get_storage

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOGS_DIR / "pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def run_discovery(seed_file: str, max_depth: int = 3):
    """Run URL discovery stage."""
    from src.stage1.discover import discover_urls

    logger.info("=== STAGE 1: URL DISCOVERY ===")
    urls = discover_urls(seed_file, max_depth)

    storage = get_storage()
    storage.write(urls, mode="append")

    logger.info(f"✅ Discovered and stored {len(urls)} URLs")
    return urls


def run_validation():
    """Run URL validation stage."""
    from src.stage2.validate import validate_urls

    logger.info("=== STAGE 2: URL VALIDATION ===")

    storage = get_storage()
    urls = storage.read()

    if not urls:
        logger.warning("No URLs to validate")
        return []

    results = validate_urls([u["url"] for u in urls])
    storage.write(results, mode="append")

    valid_count = sum(1 for r in results if r["is_valid"])
    logger.info(f"✅ Validated {len(results)} URLs ({valid_count} valid)")
    return results


def run_enrichment():
    """Run content enrichment stage."""
    from src.stage3.enrich import enrich_content

    logger.info("=== STAGE 3: CONTENT ENRICHMENT ===")

    storage = get_storage()
    data = storage.read()

    # Get valid URLs
    valid_urls = [d["url"] for d in data if d.get("is_valid")]

    if not valid_urls:
        logger.warning("No valid URLs to enrich")
        return []

    enriched = enrich_content(valid_urls)
    storage.write(enriched, mode="append")

    logger.info(f"✅ Enriched {len(enriched)} pages")
    return enriched


def main():
    parser = argparse.ArgumentParser(description="UConn Scraping Pipeline")
    parser.add_argument("--stage", choices=["1", "2", "3", "all"], default="all",
                        help="Which stage to run")
    parser.add_argument("--seed", default="data/raw/uconn_urls.csv",
                        help="Seed URLs file for discovery")
    parser.add_argument("--max-depth", type=int, default=3,
                        help="Maximum crawl depth")

    args = parser.parse_args()

    try:
        if args.stage in ["1", "all"]:
            run_discovery(args.seed, args.max_depth)

        if args.stage in ["2", "all"]:
            run_validation()

        if args.stage in ["3", "all"]:
            run_enrichment()

        logger.info("✅ Pipeline completed successfully")

        # Checkpoint on exit
        storage = get_storage()
        storage.checkpoint()

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        get_storage().checkpoint()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        get_storage().checkpoint()
        sys.exit(1)


if __name__ == "__main__":
    main()
