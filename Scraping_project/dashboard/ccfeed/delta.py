"""Live polling of one Delta table without rereading it.

Counts and the newest write come from add-action stats (``num_records``,
``max._ingestion_time``). New rows are read only from data files that were not
there last poll, with column projection, and only when the file's stats say it
can hold rows newer than the watermark. Rows at or before the watermark are
dropped, so compaction and MERGE rewrites do not replay history.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .stages import TS_COLUMN, parse_ts

logger = logging.getLogger(__name__)

NEW_ROWS_CAP = 500
RECENT_ROWS = 50
RECENT_FILES_MAX = 8


@dataclass
class WatchResult:
    status: str  # ok | missing | error
    count: int | None = None
    version: int | None = None
    last_write: float | None = None
    new_rows: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class _File:
    path: str
    partitions: dict
    num_records: int | None
    max_ts: float | None
    modified: int


class TableWatcher:
    def __init__(self, path, wanted_columns, ts_column: str = TS_COLUMN,
                 cap: int = NEW_ROWS_CAP, recent: int = RECENT_ROWS):
        self.path = Path(path)
        self.wanted = tuple(wanted_columns)
        self.ts_column = ts_column
        self.cap = cap
        self.recent = recent
        self.recent_rows: list[dict] = []
        self.version: int | None = None
        self._dt = None
        self._known: set[str] = set()
        self._watermark: float | None = None
        self._count: int | None = None
        # A table that appears after we started watching streams its rows; one that
        # was already there at startup only seeds the recent list.
        self._seen_missing = False
        self._bad: dict[str, str] = {}  # data files that exist but could not be read; retried each poll
        self._lock = threading.Lock()

    # ---- polling ----
    def poll(self) -> WatchResult:
        with self._lock:
            return self._poll()

    def _poll(self) -> WatchResult:
        first = self._dt is None
        try:
            if first:
                if not (self.path / "_delta_log").is_dir():
                    self._seen_missing = True
                    return WatchResult("missing")
                from deltalake import DeltaTable
                from deltalake.exceptions import TableNotFoundError
                try:
                    self._dt = DeltaTable(str(self.path))
                except TableNotFoundError:
                    self._seen_missing = True
                    return WatchResult("missing")
            else:
                self._dt.update_incremental()
            version = self._dt.version()
            if not first and version == self.version and not self._bad:
                return self._result([])
            files = self._files()
        except Exception as e:
            if first:
                self._dt = None
            return WatchResult("error", self._count, self.version, self._watermark,
                               error=f"{type(e).__name__}: {e}")

        self.version = version
        self._count = self._count_rows(files)
        stats_max = max((f.max_ts for f in files.values() if f.max_ts is not None), default=None)
        if first and not self._seen_missing:
            self.recent_rows, bad = self._seed_recent(files)
            self._bad = bad
            seen = max((r["_t"] for r in self.recent_rows), default=None)
            self._watermark = _max(stats_max, seen)
            self._known = set(files) - set(bad)
            return self._result([], bad)

        fresh = {key: f for key, f in files.items() if key not in self._known
                 and (key in self._bad or f.max_ts is None or self._watermark is None or f.max_ts > self._watermark)}
        rows, bad = self._read_rows(fresh)
        self._bad = bad
        new_rows = [r for r in rows if self._watermark is None or r["_t"] > self._watermark]
        new_rows.sort(key=lambda r: r["_t"])
        new_rows = new_rows[-self.cap:]
        # Unreadable files stay "unseen" so the next poll retries them, and their stats
        # don't move the watermark (their rows must still count as new once readable).
        self._known = set(files) - set(bad)
        good_max = max((f.max_ts for k, f in files.items() if k not in bad and f.max_ts is not None), default=None)
        self._watermark = _max(self._watermark, good_max, new_rows[-1]["_t"] if new_rows else None)
        if new_rows:
            self.recent_rows = (list(reversed(new_rows)) + self.recent_rows)[: self.recent]
        return self._result(new_rows, bad)

    def _result(self, new_rows: list[dict], bad: dict[str, str] | None = None) -> WatchResult:
        if bad:
            first = next(iter(bad.values()))
            more = f" (+{len(bad) - 1} more files)" if len(bad) > 1 else ""
            return WatchResult("error", self._count, self.version, self._watermark, new_rows, error=first + more)
        return WatchResult("ok", self._count, self.version, self._watermark, new_rows)

    # ---- add actions ----
    def _files(self) -> dict[str, _File]:
        actions = pa.table(self._dt.get_add_actions(flatten=True))
        names = set(actions.column_names)
        part_cols = [c for c in names if c.startswith("partition.")]
        ts_stat = f"max.{self.ts_column}"
        wanted = ["path", "num_records", "modification_time", *part_cols]
        if ts_stat in names:
            wanted.append(ts_stat)
        out: dict[str, _File] = {}
        for row in actions.select([c for c in wanted if c in names]).to_pylist():
            rel = row["path"]
            out[rel] = _File(
                path=self._abs(rel),
                partitions={c.split(".", 1)[1]: row[c] for c in part_cols},
                num_records=row.get("num_records"),
                max_ts=parse_ts(row.get(ts_stat)),
                modified=row.get("modification_time") or 0,
            )
        return out

    def _abs(self, rel: str) -> str:
        parts = urlsplit(rel)
        if parts.scheme == "file":
            return unquote(parts.path)
        if rel.startswith("/"):
            return rel
        return str(self.path / unquote(rel))

    def _count_rows(self, files: dict[str, _File]) -> int:
        total = 0
        for f in files.values():
            if f.num_records is not None:
                total += int(f.num_records)
                continue
            try:
                total += pq.ParquetFile(f.path).metadata.num_rows
            except OSError:
                pass
        return total

    # ---- reading ----
    def _read_file(self, file_path: str, columns: tuple[str, ...]) -> pa.Table:
        names = set(pq.read_schema(file_path).names)
        return pq.read_table(file_path, columns=[c for c in columns if c in names])

    def _read_rows(self, files: dict[str, _File]) -> tuple[list[dict], dict[str, str]]:
        """Rows from these files, plus {key: error} for files that exist but can't be read."""
        rows: list[dict] = []
        bad: dict[str, str] = {}
        for key, f in files.items():
            try:
                table = self._read_file(f.path, self.wanted)
            except FileNotFoundError as e:  # vacuumed or compacted away since listing
                logger.debug("skip vanished data file %s: %s", f.path, e)
                continue
            except Exception as e:  # corrupt or unreadable: report it, keep the rest
                logger.warning("unreadable data file %s: %s", f.path, e)
                bad[key] = f"{type(e).__name__}: {e}"
                continue
            for name, value in f.partitions.items():
                if name in self.wanted and name not in table.column_names:
                    table = table.append_column(name, pa.array([value] * table.num_rows, pa.string()))
            for row in table.to_pylist():
                t = parse_ts(row.get(self.ts_column))
                if t is not None:
                    row["_t"] = t
                    rows.append(row)
        return rows, bad

    def _seed_recent(self, files: dict[str, _File]) -> tuple[list[dict], dict[str, str]]:
        newest = sorted(files.items(), key=lambda kv: kv[1].modified, reverse=True)
        chosen, total = {}, 0
        for key, f in newest[:RECENT_FILES_MAX]:
            chosen[key] = f
            total += f.num_records or 0
            if total >= self.recent:
                break
        rows, bad = self._read_rows(chosen)
        rows.sort(key=lambda r: r["_t"], reverse=True)
        return rows[: self.recent], bad

    # ---- queries ----
    def table(self, columns, url: str | None = None) -> pa.Table | None:
        """Whole-table read of the given columns (those that exist), optionally one URL."""
        with self._lock:
            if self._dt is None:
                return None
            dataset = self._dt.to_pyarrow_dataset()
        cols = [c for c in columns if c in dataset.schema.names]
        filt = (pc.field("url") == url) if url is not None and "url" in dataset.schema.names else None
        return dataset.to_table(columns=cols, filter=filt)


def _max(*values):
    present = [v for v in values if v is not None]
    return max(present) if present else None
