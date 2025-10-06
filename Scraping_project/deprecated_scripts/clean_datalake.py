#!/usr/bin/env python3
"""Delta Lake Cleanup Utility

Safely drains and re-initializes the Delta Lake for clean test runs.
Includes confirmation prompt to prevent accidental data loss.
"""

import shutil
import sys
from pathlib import Path


def main():
    """Main entry point for Delta Lake cleanup."""
    # Define the path to your Delta Lake directory
    project_root = Path(__file__).parent
    delta_lake_path = project_root / "data" / "delta_lake"

    print("=" * 80)
    print("DELTA LAKE CLEANUP UTILITY")
    print("=" * 80)
    print(f"\nTarget: {delta_lake_path}")

    if delta_lake_path.exists():
        # Count files and directories
        file_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_file())
        dir_count = sum(1 for _ in delta_lake_path.rglob('*') if _.is_dir())

        print(f"Current contents: {dir_count} directories, {file_count} files")
        print("\n⚠️  WARNING: This will PERMANENTLY DELETE all Delta Lake data!")
        print("This includes:")
        print("  - stage1_discovery")
        print("  - stage1_errors")
        print("  - stage1_js_render_queue")
        print("  - stage2_page_analysis")
        print("  - stage3_summaries")
        print("  - stage4_large_docs")
        print("  - All other Delta tables and transaction logs")

        # Confirmation prompt
        print("\n" + "=" * 80)
        confirm = input("Type 'yes' to confirm deletion (anything else to cancel): ")

        if confirm.lower() == 'yes':
            print(f"\n🗑️  Draining Delta Lake at {delta_lake_path}...")

            try:
                # Remove the entire directory
                shutil.rmtree(delta_lake_path)
                print("✅ Delta Lake data deleted successfully")

                # Recreate the empty directory
                delta_lake_path.mkdir(parents=True, exist_ok=True)
                print(f"✅ Empty Delta Lake directory recreated at {delta_lake_path}")

                print("\n" + "=" * 80)
                print("CLEANUP COMPLETE - Delta Lake is ready for a fresh start")
                print("=" * 80)

            except Exception as e:
                print(f"\n❌ ERROR: Failed to clean Delta Lake: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print("\n❌ Cleanup cancelled - no changes made")
            sys.exit(0)
    else:
        print("\n⚠️  Delta Lake directory not found")

        # Create the directory
        try:
            delta_lake_path.mkdir(parents=True, exist_ok=True)
            print(f"✅ Created new Delta Lake directory at {delta_lake_path}")
        except Exception as e:
            print(f"\n❌ ERROR: Failed to create Delta Lake directory: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Cleanup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
