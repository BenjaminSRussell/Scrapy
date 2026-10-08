#!/usr/bin/env python3
"""Drain pipeline queue tables, guarded (#522, #576).

The pipeline's queues are Delta tables, not Redis lists (the Redis version of
this tool imported modules that no longer exist and could not start):

* transient: ``stage2_queue`` (Stage 1 -> 2), ``js_spider_queue`` (JS rendering)
* persistent: ``stage4_large_docs`` (Stage 4 backlog; only with ``--include-persistent``)

Draining is a Delta DELETE of every row: schema and history stay, so a drain
can be undone with ``--restore <table> --to-version <v>`` (the pre-drain
version is printed and written to the audit log). Default is a dry run;
executing needs ``--execute --i-really-mean-it`` plus ``ALLOW_LAKE_RESET=1``,
and with ``ENV=production`` also ``--break-glass`` and a typed confirmation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.destructive_guard import DestructiveOpRefused, add_guard_arguments, audit, authorize
from src.utils.lake_inventory import TableInfo, describe_table, format_inventory, resolve_lake_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TRANSIENT_QUEUES = ("stage2_queue", "js_spider_queue")
PERSISTENT_QUEUES = ("stage4_large_docs",)
ALL_QUEUES = TRANSIENT_QUEUES + PERSISTENT_QUEUES


def queue_tables(lake: Path, names: tuple[str, ...] = ALL_QUEUES) -> list[TableInfo]:
    return [describe_table(lake / n) for n in names if (lake / n / "_delta_log").is_dir()]


def select_targets(args: argparse.Namespace) -> list[str]:
    if args.queue:
        unknown = [q for q in args.queue if q not in ALL_QUEUES]
        if unknown:
            raise SystemExit(f"not a queue table: {', '.join(unknown)} (queues: {', '.join(ALL_QUEUES)})")
        blocked = [q for q in args.queue if q in PERSISTENT_QUEUES and not args.include_persistent]
        if blocked:
            raise SystemExit(f"{', '.join(blocked)} is persistent; add --include-persistent to drain it")
        return list(args.queue)
    if args.drain_all:
        return list(ALL_QUEUES if args.include_persistent else TRANSIENT_QUEUES)
    return list(TRANSIENT_QUEUES)


def drain_table(path: Path) -> int:
    """Delete every row (Delta DELETE commit); returns the new version."""
    from deltalake import DeltaTable

    dt = DeltaTable(str(path))
    dt.delete()
    return dt.version()


def restore_table(path: Path, version: int) -> int:
    from deltalake import DeltaTable

    dt = DeltaTable(str(path))
    dt.restore(version)
    return dt.version()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drain pipeline queue tables (dry run unless --execute)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python drain_lake.py --list
  python drain_lake.py                                  # dry run: transient queues
  ALLOW_LAKE_RESET=1 python drain_lake.py --execute --i-really-mean-it
  ALLOW_LAKE_RESET=1 python drain_lake.py --queue stage2_queue --execute --i-really-mean-it
  ALLOW_LAKE_RESET=1 python drain_lake.py --drain-all --include-persistent --execute --i-really-mean-it
  ALLOW_LAKE_RESET=1 python drain_lake.py --restore stage2_queue --to-version 41 --execute --i-really-mean-it
        """,
    )
    parser.add_argument("--list", "-l", action="store_true", help="List queue tables with row estimates and exit")
    parser.add_argument("--drain-transient", "-t", action="store_true", help="Drain transient queues (the default selection)")
    parser.add_argument("--drain-all", "-a", action="store_true", help="Drain all queues (persistent only with --include-persistent)")
    parser.add_argument("--queue", "-q", action="append", help="Drain a specific queue table (repeatable)")
    parser.add_argument("--include-persistent", action="store_true", help="Allow draining persistent queues (stage4_large_docs)")
    parser.add_argument("--restore", metavar="TABLE", help="Restore a queue table to --to-version (undo a drain)")
    parser.add_argument("--to-version", type=int, help="Delta version for --restore")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Kept for compatibility; dry run is already the default")
    parser.add_argument("--lake", type=Path, default=None, help=argparse.SUPPRESS)
    add_guard_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lake: Path = args.lake or resolve_lake_path()

    if args.list:
        print(f"Queue tables under {lake}:")
        print(format_inventory(queue_tables(lake)))
        print(f"persistent (never drained by default): {', '.join(PERSISTENT_QUEUES)}")
        return 0

    if args.restore:
        if args.restore not in ALL_QUEUES or args.to_version is None:
            raise SystemExit("--restore needs a queue table and --to-version")
        targets = [args.restore]
        operation = "restore-queue"
        print(f"Would restore {args.restore} to version {args.to_version}")
    else:
        targets = select_targets(args)
        operation = "drain-queues"
        present = queue_tables(lake, tuple(targets))
        print(f"Queue tables to drain under {lake}:")
        print(format_inventory(present))
        targets = [t.name for t in present]
        if not targets:
            print("Nothing to drain.")
            return 0

    try:
        execute = args.execute and not args.dry_run
        if not authorize(operation, targets, execute=execute, i_really_mean_it=args.i_really_mean_it, break_glass=args.break_glass):
            print("\nDry run only; nothing changed. Re-run with --execute --i-really-mean-it and ALLOW_LAKE_RESET=1.")
            return 0
    except DestructiveOpRefused as e:
        logger.error(str(e))
        return 3

    results: dict[str, dict[str, object]] = {}
    try:
        for name in targets:
            path = lake / name
            before = describe_table(path)
            if operation == "restore-queue":
                after = restore_table(path, args.to_version)
            else:
                after = drain_table(path)
            results[name] = {"version_before": before.version, "rows_before": before.rows, "version_after": after}
            print(f"{'Restored' if operation == 'restore-queue' else 'Drained'} {name}: version {before.version} -> {after}"
                  + ("" if operation == "restore-queue" else f" (undo: --restore {name} --to-version {before.version})"))
    except Exception as e:
        audit(operation, targets, "failed", dry_run=False, details={"results": results, "error": repr(e)})
        logger.error(f"{operation} failed: {e}")
        return 1
    audit(operation, targets, "executed", dry_run=False, details={"results": results})
    return 0


if __name__ == "__main__":
    sys.exit(main())
