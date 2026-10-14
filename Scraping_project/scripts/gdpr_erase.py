#!/usr/bin/env python3
"""Delete stage-table rows by url_hash and tombstone them (#688).

Dry-run is the default and writes nothing. ``--execute`` deletes matching
rows, appends a tombstone (re-ingest blocked until the TTL), and appends one
JSON line to ``<lake>/_audit/gdpr_erasure.jsonl``.

Legal review is still required. This is an operational stub, not a
determination that erasure satisfies GDPR or university policy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lakehouse.gdpr_erasure import DEFAULT_TTL_SECONDS, LEGAL_NOTE, erase_url_hashes  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=LEGAL_NOTE)
    parser.add_argument(
        "--url-hash",
        action="append",
        required=True,
        dest="url_hashes",
        help="Stored url_hash to erase (repeatable). Not a URL.",
    )
    parser.add_argument(
        "--ttl-days",
        type=float,
        default=DEFAULT_TTL_SECONDS / 86400,
        help="How long the tombstone blocks re-ingest (default 30).",
    )
    parser.add_argument("--actor", default="operator", help="Recorded on the tombstone and audit line.")
    parser.add_argument(
        "--reason",
        default="operator erasure request",
        help="Recorded on the tombstone and audit line. Do not put page content here.",
    )
    parser.add_argument(
        "--base-path",
        default=None,
        help="Delta lake directory. Default is DELTA_LAKE_PATH or delta_lake.base_path.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report matches only (default).")
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Delete matching rows, write tombstones, and append an audit log entry.",
    )
    return parser


def format_report(result: dict) -> str:
    lines = [LEGAL_NOTE, ""]
    verb = "DRY-RUN" if result["dry_run"] else "EXECUTED"
    lines.append(
        f"{verb} {len(result['url_hashes'])} url_hash(es); "
        f"ttl_seconds={result['ttl_seconds']}; expires_at={result['expires_at']}"
    )
    matched = result["matched"]
    if not matched:
        lines.append("matched: none")
    else:
        for table, count in sorted(matched.items()):
            lines.append(f"matched {table}: {count}")
    if result["dry_run"]:
        lines.append("no rows deleted; pass --execute to delete, tombstone, and audit")
    else:
        for table, count in sorted(result["deleted"].items()):
            lines.append(f"deleted {table}: {count}")
        lines.append(f"tombstones_written={result['tombstones_written']}")
        lines.append(f"audit={result['audit_path']}")
    for table, err in sorted(result.get("errors") or {}.items()):
        lines.append(f"error {table}: {err}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.ttl_days <= 0:
        parser.error("--ttl-days must be > 0")
    ttl_seconds = int(args.ttl_days * 86400)
    if ttl_seconds < 1:
        parser.error("--ttl-days is too small")

    from src.lakehouse.lakehouse_manager import LakehouseManager

    manager = LakehouseManager(base_path=args.base_path, start_workers=False)
    try:
        result = erase_url_hashes(
            manager,
            args.url_hashes,
            execute=bool(args.execute),
            ttl_seconds=ttl_seconds,
            actor=args.actor,
            reason=args.reason,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(LEGAL_NOTE, file=sys.stderr)
        return 2
    print(format_report(result))
    if result.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
