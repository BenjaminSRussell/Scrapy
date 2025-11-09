#!/usr/bin/env python
"""Simple test to queue URLs directly to stage2_queue without running full spider"""
import sys
from pathlib import Path
from datetime import datetime

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta

def queue_test_urls(count=10):
    """Manually queue some URLs to stage2_queue for testing"""
    print(f"📥 Manually queueing {count} URLs to stage2_queue for testing")

    # Read seed URLs
    delta = get_delta()
    try:
        seeds = delta.read_table('seed_urls')
        urls = [item['url'] for item in seeds[:count]]
        print(f"✅ Loaded {len(urls)} URLs from seed_urls")
    except Exception as e:
        print(f"⚠️ Error loading seeds: {e}")
        # Fallback to manual URLs
        urls = [
            "https://uconn.edu/",
            "https://uconn.edu/about-us/",
            "https://uconn.edu/academics/",
            "https://today.uconn.edu/",
            "https://magazine.uconn.edu/",
        ][:count]
        print(f"📝 Using fallback URLs: {len(urls)}")

    # Create queue items
    queue_items = []
    now = datetime.now().isoformat()

    for url in urls:
        queue_items.append({
            'url': url,
            'parent_url': 'manual_test',
            'content_hint': 'html',
            'status': 'pending',
            'queued_at': now,
            'queued_by': 'manual_test',
        })

    # Write to stage2_queue
    try:
        delta.write('stage2_queue', queue_items, mode='append')
        print(f"✅ Successfully queued {len(queue_items)} URLs to stage2_queue")

        # Verify
        queue = delta.read_table('stage2_queue')
        print(f"📊 stage2_queue now has {len(queue)} total items")
        print(f"   - Pending: {sum(1 for item in queue if item.get('status') == 'pending')}")

        return True
    except Exception as e:
        print(f"❌ Error writing to stage2_queue: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    success = queue_test_urls(count)
    sys.exit(0 if success else 1)
