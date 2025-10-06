#!/usr/bin/env python3
"""Drain Lake Utility - Selective queue draining for Redis message queues.

This utility allows selective clearing of transient queues while preserving
persistent queues (like Stage 4 large document processing).
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.config import get_config
from src.common.redis_manager import get_redis_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LakeDrainer:
    """Manages selective draining of Redis queues."""

    def __init__(self):
        """Initialize drainer with config."""
        self.config = get_config()

        redis_config = self.config.redis_config
        self.redis = get_redis_manager(
            host=redis_config.get('host', 'localhost'),
            port=redis_config.get('port', 6379),
            db=redis_config.get('db', 0),
            password=redis_config.get('password'),
        )

        # Get queue configuration
        mq_config = self.config.message_queue_config
        self.persistent_queues = set(mq_config.get('persistent_queues', []))
        self.transient_queues = set(mq_config.get('transient_queues', []))

    def list_queues(self):
        """List all queues with their sizes."""
        print("\n" + "="*70)
        print("REDIS QUEUE STATUS")
        print("="*70 + "\n")

        stats = self.redis.get_all_queue_stats()

        if not stats:
            print("No queues found.")
            return

        # Separate persistent and transient
        persistent = []
        transient = []
        other = []

        for queue_name, size in stats.items():
            if queue_name in self.persistent_queues:
                persistent.append((queue_name, size, 'PERSISTENT'))
            elif queue_name in self.transient_queues:
                transient.append((queue_name, size, 'TRANSIENT'))
            else:
                other.append((queue_name, size, 'UNKNOWN'))

        # Print persistent queues
        if persistent:
            print("🔒 PERSISTENT QUEUES (will NOT be drained):")
            print("-" * 70)
            for name, size, status in sorted(persistent):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print transient queues
        if transient:
            print("💨 TRANSIENT QUEUES (will be drained):")
            print("-" * 70)
            for name, size, status in sorted(transient):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print other queues
        if other:
            print("❓ OTHER QUEUES:")
            print("-" * 70)
            for name, size, status in sorted(other):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print priority queue
        pq_size = self.redis.get_queue_size()
        if pq_size > 0:
            print("🎯 PRIORITY QUEUE:")
            print("-" * 70)
            print(f"  pending URLs                             {pq_size:>10,} items")
            print()

    def drain_transient_queues(self, dry_run: bool = False):
        """Drain all transient queues.

        Args:
            dry_run: If True, only show what would be drained
        """
        stats = self.redis.get_all_queue_stats()
        transient_to_drain = [
            (name, size) for name, size in stats.items()
            if name in self.transient_queues
        ]

        if not transient_to_drain:
            print("\nNo transient queues to drain.")
            return

        print("\n" + "="*70)
        print("DRAINING TRANSIENT QUEUES")
        print("="*70 + "\n")

        total_items = 0

        for queue_name, size in sorted(transient_to_drain):
            total_items += size

            if dry_run:
                print(f"[DRY RUN] Would drain: {queue_name} ({size:,} items)")
            else:
                removed = self.redis.clear_queue(queue_name)
                print(f"✅ Drained: {queue_name} ({removed:,} items)")

        if dry_run:
            print(f"\n[DRY RUN] Would remove {total_items:,} total items")
        else:
            print(f"\n✅ Total items removed: {total_items:,}")

    def drain_all_queues(self, dry_run: bool = False, include_persistent: bool = False):
        """Drain all queues.

        Args:
            dry_run: If True, only show what would be drained
            include_persistent: If True, also drain persistent queues (DANGEROUS!)
        """
        stats = self.redis.get_all_queue_stats()

        if not stats:
            print("\nNo queues to drain.")
            return

        if include_persistent:
            print("\n⚠️  WARNING: This will drain ALL queues including persistent ones!")
        else:
            print("\n💨 Draining all TRANSIENT queues...")

        print("="*70 + "\n")

        total_items = 0

        for queue_name, size in sorted(stats.items()):
            # Skip persistent queues unless explicitly requested
            if not include_persistent and queue_name in self.persistent_queues:
                print(f"🔒 Skipped (persistent): {queue_name} ({size:,} items)")
                continue

            total_items += size

            if dry_run:
                print(f"[DRY RUN] Would drain: {queue_name} ({size:,} items)")
            else:
                removed = self.redis.clear_queue(queue_name)
                print(f"✅ Drained: {queue_name} ({removed:,} items)")

        # Also drain priority queue
        pq_size = self.redis.get_queue_size()
        if pq_size > 0:
            total_items += pq_size

            if dry_run:
                print(f"[DRY RUN] Would drain: priority_queue ({pq_size:,} items)")
            else:
                self.redis.clear_priority_queue()
                print(f"✅ Drained: priority_queue ({pq_size:,} items)")

        if dry_run:
            print(f"\n[DRY RUN] Would remove {total_items:,} total items")
        else:
            print(f"\n✅ Total items removed: {total_items:,}")

    def drain_specific_queue(self, queue_name: str, dry_run: bool = False):
        """Drain a specific queue by name.

        Args:
            queue_name: Name of queue to drain
            dry_run: If True, only show what would be drained
        """
        size = self.redis.get_queue_length(queue_name)

        if size == 0:
            print(f"\nQueue '{queue_name}' is empty or does not exist.")
            return

        # Check if persistent
        if queue_name in self.persistent_queues:
            print(f"\n⚠️  WARNING: '{queue_name}' is a PERSISTENT queue!")
            confirm = input("Are you sure you want to drain it? (type 'YES' to confirm): ")
            if confirm != 'YES':
                print("Aborted.")
                return

        print(f"\nDraining queue: {queue_name}")

        if dry_run:
            print(f"[DRY RUN] Would drain {size:,} items")
        else:
            removed = self.redis.clear_queue(queue_name)
            print(f"✅ Drained {removed:,} items")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Drain Lake - Selective Redis queue management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all queues
  python drain_lake.py --list

  # Drain transient queues (dry run)
  python drain_lake.py --drain-transient --dry-run

  # Drain transient queues (for real)
  python drain_lake.py --drain-transient

  # Drain specific queue
  python drain_lake.py --queue stage1_discovered_urls

  # Drain ALL queues including persistent (DANGEROUS!)
  python drain_lake.py --drain-all --include-persistent
        """
    )

    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='List all queues with their sizes'
    )

    parser.add_argument(
        '--drain-transient', '-t',
        action='store_true',
        help='Drain all transient queues (safe operation)'
    )

    parser.add_argument(
        '--drain-all', '-a',
        action='store_true',
        help='Drain all queues'
    )

    parser.add_argument(
        '--queue', '-q',
        type=str,
        help='Drain a specific queue by name'
    )

    parser.add_argument(
        '--include-persistent',
        action='store_true',
        help='Also drain persistent queues (DANGEROUS! Use with caution)'
    )

    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Show what would be drained without actually draining'
    )

    args = parser.parse_args()

    # Create drainer
    drainer = LakeDrainer()

    # Execute command
    if args.list:
        drainer.list_queues()

    elif args.drain_transient:
        drainer.drain_transient_queues(dry_run=args.dry_run)

    elif args.drain_all:
        drainer.drain_all_queues(
            dry_run=args.dry_run,
            include_persistent=args.include_persistent
        )

    elif args.queue:
        drainer.drain_specific_queue(args.queue, dry_run=args.dry_run)

    else:
        # Default action - list queues
        drainer.list_queues()
        print("\nUse --help to see available commands")


if __name__ == '__main__':
    main()
