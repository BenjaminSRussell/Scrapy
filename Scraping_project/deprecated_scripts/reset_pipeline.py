#!/usr/bin/env python3
"""Reset Pipeline - Clean Delta Lake and reload seed URLs

This script:
1. Safely cleans the Delta Lake
2. Reloads seed URLs from uconn_urls.csv
3. Prepares the pipeline for a fresh run
"""

import shutil
import sys
from pathlib import Path


def main():
    """Reset the pipeline to initial state."""
    project_root = Path(__file__).parent
    delta_lake_path = project_root / "data" / "delta_lake"
    seed_file = project_root / "data" / "raw" / "uconn_urls.csv"

    print("=" * 80)
    print("PIPELINE RESET UTILITY")
    print("=" * 80)

    # Verify seed file exists
    if not seed_file.exists():
        print(f"\n❌ ERROR: Seed file not found: {seed_file}")
        print("Please ensure uconn_urls.csv exists before resetting the pipeline.")
        sys.exit(1)

    # Count seed URLs
    with open(seed_file) as f:
        seed_count = sum(1 for line in f if line.strip() and line.strip().startswith('http'))

    print(f"\n📋 Seed file: {seed_file}")
    print(f"   URLs in seed: {seed_count}")

    # Show current Delta Lake status
    if delta_lake_path.exists():
        file_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_file())
        dir_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_dir())
        print(f"\n📊 Current Delta Lake: {dir_count} directories, {file_count} files")
    else:
        print(f"\n📊 Current Delta Lake: Not initialized")

    # Confirmation
    print("\n⚠️  WARNING: This will DELETE all Delta Lake data and start fresh!")
    print("\nAfter reset, the pipeline will:")
    print(f"  1. Start with {seed_count} seed URLs from uconn_urls.csv")
    print("  2. Run Stage 1 (Discovery) to crawl and discover new URLs")
    print("  3. Run Stage 2 (Analysis) to analyze all discovered pages")
    print("  4. Run Stage 3 (Summarization) to create summaries")

    confirm = input("\nType 'RESET' to confirm (anything else to cancel): ")

    if confirm != 'RESET':
        print("\n❌ Reset cancelled - no changes made")
        sys.exit(0)

    # Perform reset
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
        print("  1. Run: python run_pipeline.py")
        print("  2. Monitor progress in logs")
        print("  3. Export results with: python export_table.py --all")

    except Exception as e:
        print(f"\n❌ ERROR: Reset failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Reset interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
