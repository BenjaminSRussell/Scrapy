#!/usr/bin/env python3
"""Write a daily URL-scoring Parquet snapshot (#1103).

Offline. Does not crawl and does not read Redis, Kafka, or the Delta lake.
Schema, compaction, and an optional cron example: docs/scoring_snapshot.md.

Run from Scraping_project/:

    python scripts/export_scoring_snapshot.py --urls data/seeds/urls.txt
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_project_root() -> None:
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def main(argv: list[str] | None = None) -> int:
    _ensure_project_root()
    from src.common.scoring_snapshot import main as export_main

    return export_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
