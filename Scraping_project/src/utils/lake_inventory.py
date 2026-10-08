"""What a destructive lake operation would touch: tables, Delta versions, row estimates (#576)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset({"_trash", "_audit"})


def resolve_lake_path() -> Path:
    """The lake the pipeline actually writes: same rule as LakehouseManager and
    DeltaHelper (``DELTA_LAKE_PATH`` wins, then ``delta_lake.base_path``)."""
    override = os.getenv("DELTA_LAKE_PATH")
    if override:
        return Path(override)
    from src.core.config import Config

    return Path(Config.get_instance().get("delta_lake.base_path", "./data/delta_lake"))


@dataclass(frozen=True)
class TableInfo:
    name: str
    path: Path
    version: int | None
    rows: int | None  # from Delta add-action stats; None when stats are unavailable
    files: int
    size_bytes: int


def describe_table(path: Path) -> TableInfo:
    files = [p for p in path.rglob("*") if p.is_file() and "_delta_log" not in p.parts]
    size = sum(p.stat().st_size for p in files)
    version: int | None = None
    rows: int | None = None
    try:
        import pyarrow as pa
        from deltalake import DeltaTable

        dt = DeltaTable(str(path))
        version = dt.version()
        if not dt.file_uris():
            # deltalake 1.2 panics in get_add_actions() on a table with no
            # active files, which is exactly the state after a drain.
            rows = 0
        else:
            actions = pa.table(dt.get_add_actions(flatten=True))
            if "num_records" in actions.column_names:
                counts = actions.column("num_records").to_pylist()
                rows = None if any(c is None for c in counts) else int(sum(counts))
    except BaseException as exc:  # incl. pyo3 PanicException (not an Exception subclass)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # unreadable/partial table: still report files and size
    return TableInfo(path.name, path, version, rows, len(files), size)


def inventory(lake: Path) -> list[TableInfo]:
    """Every Delta table directly under ``lake`` (directories with a ``_delta_log``)."""
    if not lake.is_dir():
        return []
    return [
        describe_table(p)
        for p in sorted(lake.iterdir())
        if p.is_dir() and p.name not in SKIP_DIRS and (p / "_delta_log").is_dir()
    ]


def format_inventory(tables: list[TableInfo]) -> str:
    if not tables:
        return "  (no Delta tables)"
    lines = [f"  {'table':<32} {'version':>7} {'rows':>12} {'files':>6} {'size':>10}"]
    for t in tables:
        rows = f"{t.rows:,}" if t.rows is not None else "?"
        ver = str(t.version) if t.version is not None else "?"
        lines.append(f"  {t.name:<32} {ver:>7} {rows:>12} {t.files:>6} {t.size_bytes / 1e6:>8.1f}MB")
    known = [t.rows for t in tables if t.rows is not None]
    lines.append(f"  total: {len(tables)} tables, ~{sum(known):,} rows" + (" (some unknown)" if len(known) < len(tables) else ""))
    return "\n".join(lines)
