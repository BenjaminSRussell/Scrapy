#!/usr/bin/env python
"""Seed Delta Lake with URLs from uconn_urls.csv"""
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.lakehouse import SeedManager
from src.common.storage_manager import get_delta

def seed_urls(url_file: str, limit: int = 100):
    """Load URLs from CSV and add to Delta Lake seeds"""
    print(f"📥 Loading URLs from {url_file}")

    # Read URLs from file
    with open(url_file, 'r') as f:
        urls = [line.strip() for line in f if line.strip()][:limit]

    print(f"✅ Loaded {len(urls)} URLs")

    # Initialize Delta Lake and SeedManager
    delta = get_delta()
    seed_manager = SeedManager(delta)

    print(f"🌱 Adding URLs to seed manager...")
    try:
        result = seed_manager.add_urls_to_seeds(
            urls=urls,
            source_url="data/raw/uconn_urls.csv",
            source_spider="seed_script",
            write_uconn_urls=True,
            enqueue_stage2=False  # Don't enqueue yet, let scout process them
        )
        print(f"\n✅ Successfully seeded URLs:")
        print(f"   - Seed URLs: {result.get('seed_inserted', 0)}")
        print(f"   - UConn URLs: {result.get('uconn_inserted', 0)}")
        added = result.get('seed_inserted', 0)
    except Exception as e:
        print(f"   ⚠️ Error adding URLs: {e}")
        import traceback
        traceback.print_exc()
        added = 0

    print(f"\n📊 Seeds are now available for ScoutSpider to process")

if __name__ == "__main__":
    url_file = "data/raw/uconn_urls.csv"
    limit = 50  # Start with 50 URLs for testing

    if len(sys.argv) > 1:
        limit = int(sys.argv[1])

    seed_urls(url_file, limit)
