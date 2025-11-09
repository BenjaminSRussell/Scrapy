#!/usr/bin/env python
"""Run Stage 2 worker to analyze queued URLs"""
import asyncio
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

async def main():
    from src.stage2.stage2_worker import Stage2Worker
    from src.common.storage_manager import get_delta

    print("=" * 60)
    print("STAGE 2: PAGE ANALYSIS WORKER")
    print("=" * 60)

    # Check queue first
    delta = get_delta()
    try:
        queue = delta.read_table('stage2_queue')
        print(f"\n📊 Found {len(queue)} items in stage2_queue")
        pending = [item for item in queue if item.get('status') == 'pending']
        print(f"   - Pending: {len(pending)}")
        print(f"   - Sample URLs:")
        for item in pending[:5]:
            print(f"     • {item.get('url')}")
    except Exception as e:
        print(f"⚠️ Error reading queue: {e}")
        return

    if len(pending) == 0:
        print("\n⚠️ No pending URLs to process")
        return

    # Run worker
    print("\n🚀 Starting Stage 2 worker...")
    worker = Stage2Worker(max_concurrent=5, batch_size=10)
    await worker.run()

    # Check results
    print("\n" + "=" * 60)
    print("RESULTS:")
    print("=" * 60)

    try:
        analysis = delta.read_table('stage2_page_analysis')
        print(f"✅ stage2_page_analysis: {len(analysis)} pages analyzed")
        if len(analysis) > 0:
            print(f"\nSample analyzed pages:")
            for i, item in enumerate(analysis[:3]):
                print(f"  [{i+1}] {item.get('url')}")
                print(f"      Word count: {item.get('word_count')}")
                print(f"      Status: {item.get('status')}")
    except Exception as e:
        print(f"⚠️ No analysis results yet: {e}")

    print("\n✅ Stage 2 complete!")

if __name__ == '__main__':
    asyncio.run(main())
