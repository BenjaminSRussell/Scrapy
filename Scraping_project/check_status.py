#!/usr/bin/env python3
"""
Quick status checker for the scraping pipeline.
Shows counts for all Delta Lake tables.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.delta_lake import get_delta_manager


def main():
    print("=" * 80)
    print("PIPELINE STATUS CHECK")
    print("=" * 80)

    delta = get_delta_manager()

    tables = {
        'Stage 1 - Discovery': 'stage1_discovery',
        'Stage 1 - Errors': 'stage1_errors',
        'Stage 1 - JS Queue': 'stage1_js_render_queue',
        'Stage 2 - Analysis': 'stage2_page_analysis',
        'Stage 3 - Analytics': 'stage3_analytics',
        'Stage 4 - Large Docs Queue': 'stage4_large_docs',
        'Stage 4 - Summaries': 'stage4_summaries',
    }

    for label, table_name in tables.items():
        try:
            data = delta.read(table_name)
            count = len(data)

            # Special handling for queues
            if 'queue' in table_name.lower() or 'large_docs' in table_name:
                pending = sum(1 for r in data if r.get('status') == 'pending')
                completed = sum(1 for r in data if r.get('status') == 'completed')
                print(f"\n{label}:")
                print(f"  Total: {count}")
                print(f"  Pending: {pending}")
                print(f"  Completed: {completed}")
            else:
                print(f"\n{label}: {count} records")

        except Exception as e:
            print(f"\n{label}: No data (table not initialized)")

    print("\n" + "=" * 80)
    print("Use 'python run_pipeline.py' to start crawling")
    print("Use 'python src/stage1/js_bot.py' to process JS pages")
    print("Use 'python src/stage4/large_doc_processor.py' to process large docs")
    print("=" * 80)


if __name__ == '__main__':
    main()
