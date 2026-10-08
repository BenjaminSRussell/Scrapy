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

import os

from src.core.config import Config
from src.utils.redis import RedisHelper
from src.utils.destructive_guard import add_guard_arguments, audit, authorize, guard_flags

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


JS_PRIORITY_QUEUE = "js_spider:priority_queue"


class RedisQueues:
    """Sizes and clears the configured queue keys, whatever Redis type they are.

    Replaces the removed ``src.common.redis_manager`` (#576: the old import made
    this script, and the Helm preStop drain hook that swallows its error, a no-op).
    """

    _LENGTH = {"list": "llen", "zset": "zcard", "set": "scard", "stream": "xlen", "hash": "hlen"}

    def __init__(self, client, names):
        self.client = client
        self.names = sorted(set(names))

    def get_queue_length(self, name: str) -> int:
        kind = self.client.type(name)
        kind = kind.decode() if isinstance(kind, bytes) else str(kind)
        op = self._LENGTH.get(kind)
        return int(getattr(self.client, op)(name)) if op else 0

    def get_all_queue_stats(self) -> dict:
        """Configured queues that currently exist, with their sizes."""
        stats = {name: self.get_queue_length(name) for name in self.names}
        return {name: size for name, size in stats.items() if size}

    def clear_queue(self, name: str) -> int:
        size = self.get_queue_length(name)
        self.client.delete(name)
        return size

    def get_queue_size(self) -> int:
        return self.get_queue_length(JS_PRIORITY_QUEUE)

    def clear_priority_queue(self) -> int:
        return self.clear_queue(JS_PRIORITY_QUEUE)


class LakeDrainer:
    """Manages selective draining of Redis queues."""

    def __init__(self, client=None):
        """Initialize drainer from config.yml ``message_queues`` and ``redis`` (REDIS_* env wins)."""
        self.config = Config.get_instance()
        mq_config = self.config.get_section("message_queues") or {}
        self.persistent_queues = set(mq_config.get("persistent_queues") or [])
        self.transient_queues = set(mq_config.get("transient_queues") or [])
        names = {v for v in mq_config.values() if isinstance(v, str)}
        names |= self.persistent_queues | self.transient_queues

        if client is None:
            redis_config = self.config.get_section("redis") or {}
            port = os.getenv("REDIS_PORT") or redis_config.get("port")
            client = RedisHelper(
                host=os.getenv("REDIS_HOST") or redis_config.get("host"),
                port=int(port) if port else None,
                db=int(os.getenv("REDIS_DB") or redis_config.get("db") or 0),
                password=os.getenv("REDIS_PASSWORD") or redis_config.get("password"),
            ).client
        self.redis = RedisQueues(client, names)

    def list_queues(self):
        """List all queues with their sizes."""
        print("\n" + "=" * 70)
        print("REDIS QUEUE STATUS")
        print("=" * 70 + "\n")

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
                persistent.append((queue_name, size, "PERSISTENT"))
            elif queue_name in self.transient_queues:
                transient.append((queue_name, size, "TRANSIENT"))
            else:
                other.append((queue_name, size, "UNKNOWN"))

        # Print persistent queues
        if persistent:
            print("🔒 PERSISTENT QUEUES (will NOT be drained):")
            print("-" * 70)
            for name, size, _status in sorted(persistent):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print transient queues
        if transient:
            print("💨 TRANSIENT QUEUES (will be drained):")
            print("-" * 70)
            for name, size, _status in sorted(transient):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print other queues
        if other:
            print("❓ OTHER QUEUES:")
            print("-" * 70)
            for name, size, _status in sorted(other):
                print(f"  {name:<40} {size:>10,} items")
            print()

        # Print priority queue
        pq_size = self.redis.get_queue_size()
        if pq_size > 0:
            print("🎯 PRIORITY QUEUE:")
            print("-" * 70)
            print(f"  pending URLs                             {pq_size:>10,} items")
            print()

    def drain_targets(self, mode: str, queue: str | None = None, include_persistent: bool = False) -> list[dict]:
        """Queues (with sizes) a drain in ``mode`` would empty; used for the dry-run plan and audit."""
        stats = self.redis.get_all_queue_stats() or {}
        if mode == "transient":
            names = [n for n in stats if n in self.transient_queues]
        elif mode == "all":
            names = [n for n in stats if include_persistent or n not in self.persistent_queues]
        else:
            names = [queue] if queue else []
        targets = [{"queue": n, "rows": int(stats.get(n, self.redis.get_queue_length(n) if mode == "queue" else 0)),
                    "persistent": n in self.persistent_queues} for n in sorted(names)]
        if mode == "all":
            pq = self.redis.get_queue_size()
            if pq:
                targets.append({"queue": "priority_queue", "rows": int(pq), "persistent": False})
        return targets

    def drain_transient_queues(self, dry_run: bool = False):
        """Drain all transient queues.

        Args:
            dry_run: If True, only show what would be drained
        """
        stats = self.redis.get_all_queue_stats()
        transient_to_drain = [(name, size) for name, size in stats.items() if name in self.transient_queues]

        if not transient_to_drain:
            print("\nNo transient queues to drain.")
            return

        print("\n" + "=" * 70)
        print("DRAINING TRANSIENT QUEUES")
        print("=" * 70 + "\n")

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

        print("=" * 70 + "\n")

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

    def drain_specific_queue(self, queue_name: str, dry_run: bool = False, confirmed: bool = False):
        """Drain a specific queue by name.

        Args:
            queue_name: Name of queue to drain
            dry_run: If True, only show what would be drained
            confirmed: The CLI guard already obtained confirmation (skip the extra prompt)
        """
        size = self.redis.get_queue_length(queue_name)

        if size == 0:
            print(f"\nQueue '{queue_name}' is empty or does not exist.")
            return

        # Check if persistent
        if queue_name in self.persistent_queues and not dry_run and not confirmed:
            print(f"\n⚠️  WARNING: '{queue_name}' is a PERSISTENT queue!")
            confirm = input("Are you sure you want to drain it? (type 'YES' to confirm): ")
            if confirm != "YES":
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

  # Drain transient queues (for real: typed confirmation, audited)
  python drain_lake.py --drain-transient --confirm

  # Without --confirm a drain only prints the plan and exits 2.
  # ENV=production also needs --i-know-what-im-doing and ALLOW_LAKE_RESET=1.

  # Drain specific queue
  python drain_lake.py --queue stage1_discovered_urls --confirm

  # Drain ALL queues including persistent (DANGEROUS!)
  python drain_lake.py --drain-all --include-persistent --confirm
        """,
    )

    parser.add_argument("--list", "-l", action="store_true", help="List all queues with their sizes")

    parser.add_argument(
        "--drain-transient",
        "-t",
        action="store_true",
        help="Drain all transient queues (safe operation)",
    )

    parser.add_argument("--drain-all", "-a", action="store_true", help="Drain all queues")

    parser.add_argument("--queue", "-q", type=str, help="Drain a specific queue by name")

    parser.add_argument(
        "--include-persistent",
        action="store_true",
        help="Also drain persistent queues (DANGEROUS! Use with caution)",
    )

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be drained without actually draining",
    )

    add_guard_arguments(parser)

    args = parser.parse_args()

    # Create drainer
    drainer = LakeDrainer()

    if args.list or not (args.drain_transient or args.drain_all or args.queue):
        drainer.list_queues()
        if not args.list:
            print("\nUse --help to see available commands")
        return 0

    if args.drain_transient:
        mode, action = "transient", "drain transient Redis queues"
    elif args.drain_all:
        mode = "all"
        action = "drain ALL Redis queues" + (" including persistent" if args.include_persistent else "")
    else:
        mode, action = "queue", f"drain Redis queue {args.queue}"

    if args.dry_run:  # explicit preview: unguarded, exit 0
        decision = None
    else:
        # Dry-run unless --confirm; dual confirmation; production break-glass; audited (#522, #573, #576).
        targets = drainer.drain_targets(mode, args.queue, args.include_persistent)
        decision = authorize(action, targets=targets, **guard_flags(args))
        if not decision.proceed:
            return decision.exit_code

    dry = decision is None
    if mode == "transient":
        drainer.drain_transient_queues(dry_run=dry)
    elif mode == "all":
        drainer.drain_all_queues(dry_run=dry, include_persistent=args.include_persistent)
    else:
        drainer.drain_specific_queue(args.queue, dry_run=dry, confirmed=not dry)
    if decision is not None:
        audit(action, "completed", targets=decision.targets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
